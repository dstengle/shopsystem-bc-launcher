"""AgentSessionMixin for BcContainerController (controller.py decomposition, Phase 2).

Split from the former monolithic BcContainerController. Combined back into
the single public class in bc_launcher.controller.core; methods call each
other through ``self`` exactly as before.
"""
from __future__ import annotations
import os
from pathlib import Path

from bc_launcher.constants import (
    AGENT_CONTAINER_USER,
    AGENT_TMUX_SESSION,
    MAX_ARG_STRLEN,
    SHOPMSG_DSN_ENV,
)
from bc_launcher.controller._result import (
    CommandResult,
)
from bc_launcher.diagnostics import (
    CAUSE_MARKER_AGENT_STARTUP,
    CAUSE_MARKER_AGENT_VAULT,
    CAUSE_MARKER_MESSAGING_DB,
    CAUSE_MARKER_READINESS,
    launch_diagnostic_path,
)
from bc_launcher.fabro import (
    FABRO_DISPATCHER_FILE,
    LAUNCH_PATH_FABRO,
    LAUNCH_PATH_TMUX,
    _fabro_engage_script,
    _fabro_exec_env,
    _fabro_run_argv,
    _fabro_server_start_argv,
)
from bc_launcher.manifest import (
    ManifestProductTypeError,
    _read_product_from_manifest,
)
from bc_launcher.naming import (
    _container_name,
)
from bc_launcher.networking import (
    DEFAULT_SYSTEM_SLUG,
    SHOPMSG_SYSTEM_SLUG_ENV,
    resolve_probe_broker_address,
)
from bc_launcher.readiness import (
    CLAUDE_INPUT_READY_MARKER,
    CLAUDE_READINESS_TIMEOUT_SECONDS,
    CLAUDE_READY_MARKER,
    ESCAPE_AFFORDANCE_MARKER,
    ESCAPE_KEY_NAME,
    OPTION_SCREEN_MARKER,
    READINESS_DISMISS_POLL_SECONDS,
    _readiness_wait_blocking_prompt,
)


class AgentSessionMixin:

    def _start_agent_session(
        self,
        bc_name: str,
        container: str,
        startup_prompt: str | None,
        dsn: str | None,
        probe_broker_address: str,
        out_lines: list[str],
        err_lines: list[str],
        launch_path: str = LAUNCH_PATH_TMUX,
        work_id: str | None = None,
    ) -> CommandResult:
        """Drive the agent-start sequence against an already-provisioned
        container: start the agent tmux session, gate on the two readiness
        barriers, start ``agent-vault run -- claude``, wait for the readiness
        markers, and inject the startup prompt.

        SHARED by ``launch`` (after clone + provisioning) and ``start_agent``
        (recovery against an already-cloned container).  Sharing the sequence
        keeps the readiness barriers and inject ordering identical across both
        entry points (lead-k4k7).  ``out_lines`` / ``err_lines`` accumulate the
        result's stdout / stderr; the caller passes whatever preamble it has
        already logged.

        ENGAGE TIER (lead-cadr — S4).  ``launch_path`` selects the engage tier
        AFTER the readiness barriers pass; the barriers themselves and every
        launch-parity surface (container / credential-proxy / postgres DSN /
        shop-msg mailbox) are IDENTICAL on both paths (ADR-050 D1/D2):

          * ``launch_path == "tmux"`` (DEFAULT): the EXISTING tmux ``agent``
            send-keys / ``agent-vault run -- claude`` engage, UNCHANGED
            (scenario 04, @scenario_hash:04236074a60ffcd7).  NO fabro server,
            NO fabro run.
          * ``launch_path == "fabro"``: the engage tier is REPLACED (ADR-050
            D3) by the fabro run-graph entry — the launcher starts the
            ephemeral in-container fabro server
            (``fabro server start --foreground --no-web``) and runs the placed
            ADR-051 loop def against it
            (``fabro run workflow.fabro -I BC_NAME=<bc> -I WORK_ID=<work_id>``)
            as the engage, and starts NO tmux ``agent`` send-keys session and
            NO ``claude`` engage on this path.

        ``work_id`` carries the WORK_ID into the fabro run's ``-I`` input; it
        is unused on the tmux path.
        """
        # FABRO ORCHESTRATOR ENGAGE (lead-cadr).  On the fabro path the engage
        # tier is REPLACED, not added alongside (ADR-050 D3): the launcher
        # starts NO tmux `agent` send-keys session and NO `claude` engage.
        # The readiness barriers still gate the engage (identical to the tmux
        # path — ADR-050 D1/D2 launch parity): on failure, engage NOTHING.
        if launch_path == LAUNCH_PATH_FABRO:
            return self._fabro_engage(
                bc_name,
                container,
                dsn,
                probe_broker_address,
                work_id,
                out_lines,
                err_lines,
            )

        # Start tmux session as vscode.  Claude Code refuses
        # --dangerously-skip-permissions when EUID==0 ("cannot be used with
        # root/sudo privileges for security reasons"), so the agent must
        # run as the unprivileged vscode user — and that requires the tmux
        # server itself to be vscode-owned, because tmux refuses
        # cross-user attach (any subsequent send-keys / capture-pane /
        # has-session / attach-session call against this session must
        # therefore also run as vscode).
        self._driver.exec_run(
            container,
            ["tmux", "new-session", "-d", "-s", AGENT_TMUX_SESSION],
            user=AGENT_CONTAINER_USER,
        )
        out_lines.append(f"Started tmux session '{AGENT_TMUX_SESSION}'\n")

        # Start Claude Code inside the tmux session and wait for readiness
        # before injecting any user prompt.  The default tmux session command
        # is bash; without this sequence the startup prompt lands in bash
        # ("-bash: Run: command not found") and Claude Code never starts.
        # Only run the readiness sequence when a startup_prompt will be
        # injected.  An empty startup_prompt (lead-9sq's documented opt-out)
        # skips both the prompt injection AND the Claude Code start, leaving
        # the tmux session with its default bash command — preserving the
        # legacy escape hatch.
        if startup_prompt:
            # Readiness barrier — messaging database reachability.
            #
            # Before engaging the agent we verify the messaging backend at
            # SHOPMSG_DSN is reachable.  A BC agent whose messaging DB is
            # unreachable cannot arm its inbox watcher or drain pending
            # inbox, so injecting the startup prompt would launch an agent
            # straight into a wall of connection failures.  This barrier
            # fires BEFORE any Claude Code start / prompt injection: on
            # failure we return non-zero with a stderr line naming the DSN
            # and send NOTHING to the tmux session.
            if dsn and not self._driver.messaging_db_reachable(
                dsn, container=container
            ):
                reason = (
                    f"messaging readiness failure: messaging database at "
                    f"{SHOPMSG_DSN_ENV}={dsn} is not reachable; "
                    f"startup prompt NOT injected"
                )
                err_lines.append(reason + "\n")
                self._write_launch_diagnostic(
                    bc_name, CAUSE_MARKER_MESSAGING_DB, reason, err_lines
                )
                return CommandResult(
                    exit_code=1,
                    stdout="".join(out_lines),
                    stderr="".join(err_lines),
                )

            # Readiness barrier — agent-vault broker reachability (ADR-026).
            #
            # The agent's Claude OAuth and GitHub credentials are substituted
            # by the agent-vault broker on outbound requests; an agent whose
            # broker is unreachable can authenticate to nothing.  This barrier
            # fires BEFORE any Claude Code start / prompt injection: on failure
            # we return non-zero with a stderr line naming the configured
            # broker address and send NOTHING to the tmux session.  Combined
            # with the messaging-DB barrier above, the agent engages only when
            # BOTH the messaging database AND the agent-vault broker are
            # reachable (scenarios f73afae0 / 64aaff80 / 6cb07698).
            #
            # lead-cs7k: the probe targets ``probe_broker_address`` (derived
            # from the resolved product slug, decoupled from the runtime proxy)
            # and runs from INSIDE the launched container's network context
            # (``container=container``) so its reachability matches the
            # container's, not the launcher host's.
            if not self._driver.agent_vault_reachable(
                probe_broker_address, container=container
            ):
                reason = (
                    f"agent-vault readiness failure: agent-vault broker at "
                    f"{probe_broker_address} is not reachable; "
                    f"startup prompt NOT injected"
                )
                err_lines.append(reason + "\n")
                self._write_launch_diagnostic(
                    bc_name, CAUSE_MARKER_AGENT_VAULT, reason, err_lines
                )
                return CommandResult(
                    exit_code=1,
                    stdout="".join(out_lines),
                    stderr="".join(err_lines),
                )

            # Step 1: start Claude Code, wrapped as `agent-vault run -- claude`
            # (ADR-026).  agent-vault run establishes the proxy substitution
            # context (HTTPS_PROXY is already exported into the container env
            # pointing at the broker) so the broker injects the real Claude
            # OAuth / GitHub credentials on outbound requests; the container
            # holds only the placeholder.  --dangerously-skip-permissions is
            # passed through to claude: the BC container is the isolation
            # boundary the permission prompts substitute for, so bypassing
            # them inside the container prevents the agent from hanging on
            # permission gates that have no operator at the other end.
            self._driver.exec_run(
                container,
                ["tmux", "send-keys", "-t", AGENT_TMUX_SESSION,
                 "agent-vault run -- claude --dangerously-skip-permissions",
                 "Enter"],
                user=AGENT_CONTAINER_USER,
            )
            # Step 2/3: bounded readiness wait that resolves the workspace-trust
            # gate by polling for EITHER of two markers (lead-gw9v / lead-c713),
            # integrated with — and feeding into — the step-4 input-ready loop:
            #
            #   * CLAUDE_READY_MARKER ("Accessing workspace:") — the PRE-trust
            #     banner that appears BEFORE trust is accepted.  When it is
            #     observed first, the trust prompt is live: accept it with a
            #     bare Enter (step 3) and fall through to the step-4 input-ready
            #     wait.  This is the pre-trust path and it is UNCHANGED.
            #
            #   * CLAUDE_INPUT_READY_MARKER ("bypass permissions on") — the
            #     POST-trust input-ready marker.  bc-base bakes
            #     `bypassPermissionsModeAccepted`, so claude can SELF-ADVANCE
            #     past the workspace-trust prompt straight to input-ready; the
            #     transient "Accessing workspace:" banner is then never caught
            #     by polling.  When the pane is ALREADY at input-ready, treat
            #     claude as UP: SKIP the trust-accept Enter (there is no trust
            #     prompt to accept) and proceed directly to inject — do NOT
            #     hard-require the transient banner and do NOT abort.
            #
            # The PRIOR shape hard-gated on CLAUDE_READY_MARKER and ABORTED with
            # an "agent-startup failure" the instant the transient banner was
            # not caught — which dropped every self-advancing unattended launch
            # even though claude was healthy and sitting at input-ready.  The
            # loop below removes that hard gate while keeping the pre-trust path
            # intact and bounding the whole wait by the readiness timeout.
            trust_accepted = False
            input_ready = False
            deadline = (
                self._monotonic() + CLAUDE_READINESS_TIMEOUT_SECONDS
            )
            while True:
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    break
                per_attempt = min(
                    READINESS_DISMISS_POLL_SECONDS, remaining
                )
                # Poll for the PRE-trust banner first so the pre-trust path
                # (banner observed → accept trust with Enter) is unchanged.
                banner = self._driver.wait_for_pane_marker(
                    container,
                    AGENT_TMUX_SESSION,
                    CLAUDE_READY_MARKER,
                    per_attempt,
                )
                if banner:
                    # Step 3: accept the workspace-trust prompt (default "Yes, I
                    # trust").  Empirically verified (2026-05-29) that
                    # --dangerously-skip-permissions does NOT, on its own,
                    # bypass workspace trust when the prompt IS presented; this
                    # Enter advances past it.  It fires ONLY on the pre-trust
                    # path — never when claude self-advanced (below).
                    self._driver.exec_run(
                        container,
                        ["tmux", "send-keys", "-t", AGENT_TMUX_SESSION, "Enter"],
                        user=AGENT_CONTAINER_USER,
                    )
                    trust_accepted = True
                    break
                # Banner not caught this attempt.  Capture the pane: if claude
                # has SELF-ADVANCED past the trust prompt straight to the
                # input-ready marker, treat it as up and SKIP the trust-accept
                # Enter entirely.
                pane = self._driver.capture_pane(
                    container, AGENT_TMUX_SESSION
                )
                if CLAUDE_INPUT_READY_MARKER in pane:
                    input_ready = True
                    out_lines.append(
                        "Agent self-advanced past the workspace-trust prompt "
                        "to the input-ready marker "
                        f"{CLAUDE_INPUT_READY_MARKER!r}; treating the agent as "
                        "up and skipping the trust-accept Enter (lead-gw9v)\n"
                    )
                    break
                # Neither marker yet.  Keep polling until the deadline.
                if self._monotonic() >= deadline:
                    break
            if not trust_accepted and not input_ready:
                # Neither the PRE-trust banner nor the self-advanced input-ready
                # marker was reached within the readiness timeout: claude (or
                # its tmux session) never came up.  Warn (host-discoverable)
                # and abort WITHOUT injecting.
                reason = (
                    f"agent-startup failure: Claude Code did not become ready "
                    f"within {CLAUDE_READINESS_TIMEOUT_SECONDS:.0f}s — the agent "
                    f"never reached input-ready: neither the workspace-trust "
                    f"banner {CLAUDE_READY_MARKER!r} nor the input-ready marker "
                    f"{CLAUDE_INPUT_READY_MARKER!r} was observed within the "
                    f"readiness timeout (claude or its tmux session never "
                    f"started); startup prompt NOT injected"
                )
                err_lines.append("warning: " + reason + "\n")
                self._write_launch_diagnostic(
                    bc_name, CAUSE_MARKER_AGENT_STARTUP, reason, err_lines
                )
                return CommandResult(
                    exit_code=1,
                    stdout="".join(out_lines),
                    stderr="".join(err_lines),
                )
            # Step 4: wait for the POST-trust input-ready marker, with
            # bounded auto-dismissal of unexpected interactive prompts
            # (lead-cw7m / lead-c713).  SKIPPED when claude already
            # self-advanced to input-ready above (lead-gw9v).
            #
            # CLAUDE_INPUT_READY_MARKER is "bypass permissions on" — only
            # present once the trust prompt has cleared AND
            # --dangerously-skip-permissions is active, which is the exact
            # state in which the user prompt can be safely injected.
            #
            # The new bc-base Claude Code image (c50b3b) can render an EARLIER
            # interactive prompt (e.g. "Try the new fullscreen renderer?")
            # that BLOCKS reaching input-ready.  A single narrow wait would
            # time out at 60s and never inject.  Instead, run a BOUNDED
            # scan-dismiss loop: each iteration waits for the input-ready
            # marker for a short per-attempt budget; if it does not appear,
            # capture the pane and, if it presents an Esc-dismissable prompt
            # that is NOT the workspace-trust prompt, send ONLY Esc (decline —
            # NEVER Enter / '1', so the renderer is NOT enabled), emit a
            # host-discoverable WARNING NAMING the prompt, and continue.  The
            # TOTAL elapsed time is bounded by CLAUDE_READINESS_TIMEOUT_SECONDS;
            # on timeout WITHOUT input-ready the loop STOPS attempting
            # dismissals (no infinite loop), warns, and proceeds WITHOUT
            # injecting.
            #
            # lead-gw9v: when claude SELF-ADVANCED past the trust prompt (above),
            # input_ready is already True and the agent is already at the
            # input-ready marker — there is nothing left to wait for or dismiss,
            # so this whole loop is SKIPPED and we proceed straight to inject.
            if not input_ready:
                deadline = (
                    self._monotonic() + CLAUDE_READINESS_TIMEOUT_SECONDS
                )
                while True:
                    remaining = deadline - self._monotonic()
                    if remaining <= 0:
                        break
                    per_attempt = min(
                        READINESS_DISMISS_POLL_SECONDS, remaining
                    )
                    input_ready = self._driver.wait_for_pane_marker(
                        container,
                        AGENT_TMUX_SESSION,
                        CLAUDE_INPUT_READY_MARKER,
                        per_attempt,
                    )
                    if input_ready:
                        break
                    # Input-ready not yet observed within this attempt.  Capture
                    # the pane and look for a blocking readiness-wait prompt to
                    # auto-dismiss with Esc.
                    pane = self._driver.capture_pane(
                        container, AGENT_TMUX_SESSION
                    )
                    prompt_name = _readiness_wait_blocking_prompt(pane)
                    if prompt_name is None:
                        # No Esc-dismissable prompt is blocking; nothing more to
                        # do this iteration — keep polling until the deadline.
                        if self._monotonic() >= deadline:
                            break
                        continue
                    # Send a DISCRETE send-keys carrying ONLY the Escape key
                    # payload — NOT Enter, and NOT '1'.  This declines the
                    # prompt with its own non-committal default (e.g. does NOT
                    # enable the fullscreen renderer) and lets the loop proceed.
                    self._driver.exec_run(
                        container,
                        ["tmux", "send-keys", "-t", AGENT_TMUX_SESSION,
                         ESCAPE_KEY_NAME],
                        user=AGENT_CONTAINER_USER,
                    )
                    err_lines.append(
                        "warning: an unexpected interactive prompt was "
                        "auto-dismissed during the readiness wait (sent Escape "
                        f"to the tmux session {AGENT_TMUX_SESSION!r}, NOT Enter, "
                        "so no default was confirmed and the fullscreen "
                        f"renderer was NOT enabled); the prompt was: "
                        f"{prompt_name!r} (lead-cw7m)\n"
                    )
                    out_lines.append(
                        "Auto-dismissed an unexpected interactive prompt with "
                        "Escape during the readiness wait (lead-cw7m): "
                        f"{prompt_name!r}\n"
                    )
                    # Continue the loop: re-wait for the input-ready marker.
                if not input_ready:
                    # BOUNDED: the scan-dismiss loop terminated at the 60s
                    # deadline rather than looping indefinitely.  Stop
                    # attempting dismissals, warn that the main input did not
                    # become ready, and proceed WITHOUT injecting.
                    reason = (
                        f"readiness failure: Claude Code workspace-trust prompt "
                        f"did not clear / main input did not become ready "
                        f"within {CLAUDE_READINESS_TIMEOUT_SECONDS:.0f} seconds "
                        f"(marker {CLAUDE_INPUT_READY_MARKER!r} not seen; the "
                        f"readiness barrier never reported both supporting "
                        f"servers ready); startup prompt NOT injected"
                    )
                    err_lines.append("warning: " + reason + "\n")
                    self._write_launch_diagnostic(
                        bc_name, CAUSE_MARKER_READINESS, reason, err_lines
                    )
                    return CommandResult(
                        exit_code=1,
                        stdout="".join(out_lines),
                        stderr="".join(err_lines),
                    )
            option_result = self._handle_option_screen(
                container, out_lines, err_lines
            )
            if option_result is not None:
                return option_result

            # Step 5: inject the startup prompt into Claude Code's input.
            #
            # Two DISCRETE writes (the prompt text as a paste first, a bare
            # Enter second), NOT one write carrying both (lead-lez1 / lead-9q0f
            # root cause).  A single keystream concatenated into ONE pty write()
            # is absorbed by the TUI as a paste above ~70 bytes, swallowing the
            # trailing CR into the input buffer instead of submitting.  Two
            # discrete writes separated by a kernel-scheduling gap are processed
            # as a paste followed by a discrete submit keypress.
            #
            # lead-m4zt: a startup prompt above the Linux MAX_ARG_STRLEN 128 KiB
            # per-single-argument limit can NOT ride the send-keys argv — the
            # `docker exec` spawn fails E2BIG ("Argument list too long") and the
            # prompt is never injected, so the BC never engages.  When the prompt
            # exceeds the limit, stream it OFF the argv: `tmux load-buffer -`
            # reads it from the exec's STDIN into a tmux buffer, `paste-buffer`
            # deposits it into the agent pane as the SAME single paste write, and
            # a discrete Enter send-keys submits it — preserving the
            # two-discrete-writes submit contract with no argv-carried blob.  A
            # normal-size prompt keeps the exact unchanged two-send-keys shape.
            if len(startup_prompt.encode("utf-8")) > MAX_ARG_STRLEN:
                startup_buffer = "bc-startup-prompt"
                self._driver.exec_run(
                    container,
                    ["tmux", "load-buffer", "-b", startup_buffer, "-"],
                    user=AGENT_CONTAINER_USER,
                    input=startup_prompt,
                )
                self._driver.exec_run(
                    container,
                    ["tmux", "paste-buffer", "-t", AGENT_TMUX_SESSION,
                     "-b", startup_buffer, "-d"],
                    user=AGENT_CONTAINER_USER,
                )
                self._driver.exec_run(
                    container,
                    ["tmux", "send-keys", "-t", AGENT_TMUX_SESSION, "Enter"],
                    user=AGENT_CONTAINER_USER,
                )
                out_lines.append(
                    "Injected startup prompt "
                    f"({len(startup_prompt.encode('utf-8'))} bytes) via a tmux "
                    "buffer streamed on STDIN (off the docker argv, lead-m4zt)\n"
                )
            else:
                self._driver.exec_run(
                    container,
                    ["tmux", "send-keys", "-t", AGENT_TMUX_SESSION,
                     startup_prompt],
                    user=AGENT_CONTAINER_USER,
                )
                self._driver.exec_run(
                    container,
                    ["tmux", "send-keys", "-t", AGENT_TMUX_SESSION, "Enter"],
                    user=AGENT_CONTAINER_USER,
                )
                out_lines.append(
                    f"Injected startup prompt: {startup_prompt!r}\n"
                )

        return CommandResult(
            exit_code=0, stdout="".join(out_lines), stderr="".join(err_lines)
        )
