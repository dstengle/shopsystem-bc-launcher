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
            # Step 4b: blocking interactive option-screen handling (lead-q3uy).
            #
            # After the input-ready marker but BEFORE the prompt is submitted,
            # the agent runtime can present a blocking interactive option
            # screen that absorbs keystrokes.  Capture the pane ONCE and
            # classify it:
            #   * recognized blocking option screen WITH an escape affordance →
            #     send a DISCRETE send-keys carrying ONLY the Escape key (never
            #     Enter — and never an Enter to "select a default"), capture the
            #     dismissed screen's content, log it as a host-discoverable
            #     WARNING, then fall through to submit the prompt directly;
            #   * recognized blocking option screen with NO escape affordance →
            #     do NOT send Enter / do NOT auto-confirm a default; surface a
            #     WARNING naming the un-escapable screen and do NOT submit the
            #     prompt (which the screen would swallow);
            #   * no blocking option screen → proceed to submit as normal.
            pane = self._driver.capture_pane(container, AGENT_TMUX_SESSION)
            if OPTION_SCREEN_MARKER in pane:
                if ESCAPE_AFFORDANCE_MARKER in pane:
                    # Capture the rendered content BEFORE dismissing, so the
                    # WARNING records exactly what was auto-dismissed.
                    dismissed_content = pane
                    # Discrete send-keys carrying ONLY the Escape key payload —
                    # NOT Enter, and NOT a text+Enter pair.  This dismisses the
                    # escape-able screen without selecting any default option.
                    self._driver.exec_run(
                        container,
                        ["tmux", "send-keys", "-t", AGENT_TMUX_SESSION,
                         ESCAPE_KEY_NAME],
                        user=AGENT_CONTAINER_USER,
                    )
                    err_lines.append(
                        "warning: an interactive option screen was "
                        "auto-dismissed during engage (sent Escape to the "
                        f"tmux session {AGENT_TMUX_SESSION!r}); rendered "
                        "content of the dismissed screen follows so a human "
                        "can review what was auto-dismissed (lead-q3uy):\n"
                        f"{dismissed_content}\n"
                    )
                    out_lines.append(
                        "Auto-dismissed a blocking interactive option screen "
                        "with Escape during engage (lead-q3uy)\n"
                    )
                else:
                    # No escape affordance: refuse to auto-confirm.  Pressing
                    # Enter here would blindly select whatever option is
                    # highlighted, so send NOTHING and do NOT submit the prompt.
                    err_lines.append(
                        "warning: engage encountered a blocking interactive "
                        "screen with NO escape/dismiss affordance; the launcher "
                        "did NOT send Enter and did NOT auto-confirm a default; "
                        "the startup prompt was NOT submitted.  Un-escapable "
                        "screen content follows so a human can review it from "
                        f"the host (lead-q3uy):\n{pane}\n"
                    )
                    return CommandResult(
                        exit_code=0,
                        stdout="".join(out_lines),
                        stderr="".join(err_lines),
                    )

            # Step 5: inject the startup prompt into Claude Code's input.
            #
            # Two DISCRETE send-keys invocations (text first, Enter second),
            # NOT one invocation carrying both (lead-lez1 / lead-9q0f root
            # cause).  A single `send-keys <text> Enter` exec_run concatenates
            # the whole keystream into ONE pty write() syscall; Claude Code's
            # TUI treats single-write payloads above ~70 bytes as a paste and
            # absorbs the trailing CR into the input buffer instead of
            # submitting.  Two exec_run calls are two discrete pty writes
            # separated by a kernel-scheduling gap, which the TUI processes as
            # a discrete submit keypress.
            self._driver.exec_run(
                container,
                ["tmux", "send-keys", "-t", AGENT_TMUX_SESSION, startup_prompt],
                user=AGENT_CONTAINER_USER,
            )
            self._driver.exec_run(
                container,
                ["tmux", "send-keys", "-t", AGENT_TMUX_SESSION, "Enter"],
                user=AGENT_CONTAINER_USER,
            )
            out_lines.append(f"Injected startup prompt: {startup_prompt!r}\n")

        return CommandResult(
            exit_code=0, stdout="".join(out_lines), stderr="".join(err_lines)
        )


    def _fabro_engage(
        self,
        bc_name: str,
        container: str,
        dsn: str | None,
        probe_broker_address: str,
        work_id: str | None,
        out_lines: list[str],
        err_lines: list[str],
    ) -> CommandResult:
        """Drive the FABRO orchestrator ENGAGE step (lead-cadr — S4, corrected
        by lead-odd9 / ADR-058).

        REPLACES the tmux/claude engage tier on the fabro launch path (ADR-050
        D3): AFTER the SAME readiness barriers the tmux path gates on
        (messaging DB + agent-vault broker — ADR-050 D1/D2 launch parity), the
        launcher engages by

          1. starting the EPHEMERAL in-container fabro server in the
             FOREGROUND with no web UI, bound to a local 127.0.0.1 socket
             (``fabro server start --foreground --no-web``), so the loop runs
             headless inside the one bc-base container; and
          2. running the placed REACTIVE-PERSISTENT DISPATCHER def against that
             server (``fabro run dispatcher.fabro -I BC_NAME=<bc>``) as the ONE
             persistent engage (ADR-058 D1).  It carries ONLY the constant
             BC_NAME and supplies NO ``-I WORK_ID``: the dispatcher OWNS the
             container's lifecycle and discovers work_ids at RUNTIME, fanning
             out one detached ``fabro run workflow.fabro`` child per pending
             work item.

        ``work_id`` is an IGNORED no-op on this path (ADR-058 D6): the fabro
        launch interface requires no launch-time work id, exactly like the tmux
        path.  It starts NO tmux ``agent`` send-keys session and NO ``claude``
        engage — the engage tier is REPLACED by the fabro run-graph entry, not
        added alongside it (reproduces fabro-orchestration/01
        @scenario_hash:1aeace4c593ab14f via the real bc-container launch path).
        """
        # Readiness barrier — messaging database reachability (IDENTICAL to the
        # tmux path — ADR-050 D1/D2 launch parity).  On failure engage NOTHING.
        if dsn and not self._driver.messaging_db_reachable(
            dsn, container=container
        ):
            reason = (
                f"messaging readiness failure: messaging database at "
                f"{SHOPMSG_DSN_ENV}={dsn} is not reachable; fabro engage NOT "
                f"started"
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

        # Readiness barrier — agent-vault broker reachability (IDENTICAL to the
        # tmux path — ADR-026 / ADR-050 D1/D2 launch parity).
        if not self._driver.agent_vault_reachable(
            probe_broker_address, container=container
        ):
            reason = (
                f"agent-vault readiness failure: agent-vault broker at "
                f"{probe_broker_address} is not reachable; fabro engage NOT "
                f"started"
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

        # ENGAGE (REPLACES the tmux/claude engage — ADR-050 D3).  One `/bin/sh
        # -c` script cd's into the placed def dir, starts the ephemeral fabro
        # server (foreground, no web, provider=local, 127.0.0.1), then runs the
        # loop def against it carrying BC_NAME + WORK_ID.  Runs as the vscode
        # agent user (the def + settings were placed agent-owned).  NO tmux
        # session is created and NO `claude` is started on this path.
        #
        # lead-lwk4 R7 (LAUNCH ACTUALLY RETURNS AFTER ENGAGE): issued DETACHED
        # (`docker exec -d`) so the docker daemon backgrounds the engage and this
        # call RETURNS IMMEDIATELY without reading the exec's stdout/stderr — the
        # foreground fabro server's stdio never rides the launcher's pipes, so
        # `launch()` returns after the engage is issued instead of blocking for
        # the server's lifetime.  The v0.3.49 nohup-inside-the-script fix could
        # not achieve this: backgrounded children inherit the (attached) exec
        # pipes, so a synchronous `docker exec` never sees EOF.  The fabro server
        # + run keep running headless in the container after this returns.
        engage_result = self._driver.exec_run(
            container,
            ["/bin/sh", "-c", _fabro_engage_script(bc_name)],
            user=AGENT_CONTAINER_USER,
            env=_fabro_exec_env(),
            detach=True,
        )
        if engage_result.returncode != 0:
            reason = (
                f"fabro engage failure: `fabro server start` / `fabro run "
                f"{FABRO_DISPATCHER_FILE}` exited {engage_result.returncode}: "
                f"{(engage_result.stderr or engage_result.stdout).strip()}"
            )
            err_lines.append("warning: " + reason + "\n")
            return CommandResult(
                exit_code=1,
                stdout="".join(out_lines),
                stderr="".join(err_lines),
            )
        out_lines.append(
            "Fabro orchestrator engage (lead-cadr / ADR-058): started the "
            "ephemeral in-container fabro server "
            f"({' '.join(_fabro_server_start_argv())}) and ran the PERSISTENT "
            "reactive dispatcher def as the engage ("
            f"{' '.join(_fabro_run_argv(bc_name))}); no tmux 'agent' send-keys "
            "session and no 'claude' engage started on this path — the engage "
            "tier is REPLACED by the fabro run-graph entry (ADR-050 D3)\n"
        )
        return CommandResult(
            exit_code=0, stdout="".join(out_lines), stderr="".join(err_lines)
        )


    # ------------------------------------------------------------------
    # agent-start sequence (shared by launch + start_agent, lead-k4k7)
    # ------------------------------------------------------------------

    def _write_launch_diagnostic(
        self,
        bc_name: str,
        cause_marker: str,
        reason: str,
        err_lines: list[str],
    ) -> Path | None:
        """Persist a launch-failure diagnostic FILE on the per-BC host surface.

        lead-63em.  Writes a single human-readable line carrying the literal
        ``cause_marker`` token plus ``reason`` to the documented per-BC
        host-discoverable path (``launch_diagnostic_path``).  The file is
        readable from the host WITHOUT attaching into any tmux session and
        WITHOUT relying on the launch command's stderr or the bc-container
        monitor tmux pane.

        lead-bnhn (P1 bugfix) — BEST-EFFORT / NON-FATAL.  The diagnostic write
        (its on-demand parent ``mkdir`` and the file write) is wrapped so that
        ANY write failure (``PermissionError`` / ``OSError`` from an unwritable
        target dir, a read-only filesystem, etc.) is CAUGHT here, surfaced as a
        host-discoverable WARNING on the launch result's stderr (naming that
        the diagnostic could NOT be written, the target path, and the cause),
        and then SWALLOWED so the launch is NOT aborted.  A diagnostic-write
        failure is strictly less severe than the launch failure it would
        describe; it must degrade gracefully, never escalate.  This method is
        the single choke point ALL launch-failure-diagnostic call sites pass
        through, so wrapping it here protects EVERY call site at once.

        On success the method itself appends the host-discoverable
        ``launch diagnostic persisted to <path>`` line to ``err_lines`` and
        returns the path written; on a caught write failure it appends the
        warning line and returns ``None``.  The FILE, not the stderr line, is
        the authoritative diagnostic surface when the write succeeds — but the
        stderr warning is the legible fallback when even the file cannot be
        written.
        """
        path = launch_diagnostic_path(bc_name)
        content = (
            f"cause: {cause_marker}\n"
            f"reason: {reason}\n"
        )
        try:
            self._driver.write_launch_diagnostic(str(path), content)
        except OSError as exc:
            # NON-FATAL: the diagnostic write failed (e.g. the target dir is
            # not writable — the lead-bnhn /var/lib/bc-launcher PermissionError
            # crash).  Surface a host-discoverable WARNING and CONTINUE; never
            # let the diagnostic-write failure abort the launch it describes.
            err_lines.append(
                f"warning: could not write launch diagnostic to {path}: "
                f"{type(exc).__name__}: {exc}; continuing without the "
                f"persisted diagnostic file (the launch failure cause is "
                f"reported on stderr above)\n"
            )
            return None
        err_lines.append(f"launch diagnostic persisted to {path}\n")
        return path


    # ------------------------------------------------------------------
    # start-agent — recovery: drive agent-start against an already-cloned
    # healthy container without re-cloning (lead-k4k7)
    # ------------------------------------------------------------------

    def start_agent(
        self,
        bc_name: str,
        startup_prompt: str | None = None,
        shopmsg_dsn: str | None = None,
        agent_vault_broker: str | None = None,
        manifest_path: Path | None = None,
    ) -> CommandResult:
        """Recovery subcommand: drive the agent-start sequence against an
        ALREADY-cloned, healthy container that has no agent — WITHOUT
        re-cloning.

        lead-k4k7.  Makes first-class the manual recovery the lead performed
        when a transient skill-refresh failure stranded a fully-cloned
        "Up (healthy)" container with no agent session.  It runs the SAME
        agent-start sequence ``launch`` uses (``_start_agent_session``): tmux
        new-session as vscode, the messaging-DB + agent-vault readiness
        barriers, ``agent-vault run -- claude``, the readiness-marker waits,
        and the prompt injection — but NO clone, NO beads provisioning, and NO
        skill-refresh.  It is idempotent / safe to re-run on a container
        stranded with a clone but no agent.

        Resolution of the readiness-probe inputs mirrors ``launch``: the DSN
        comes from ``shopmsg_dsn`` (falling back to the container's recorded
        DSN, then the ``SHOPMSG_DSN`` process env), and the probe broker is
        derived from the resolved product slug (an explicit broker still wins).
        """
        container = _container_name(bc_name)

        if not self._driver.is_running(container):
            return CommandResult(
                exit_code=1,
                stderr=(
                    f"{container} is not running; start-agent recovers an "
                    "already-cloned, healthy container that has no agent — "
                    "run `bc-container launch` first to create it\n"
                ),
            )

        # lead-pixf (aeebb281): detect an ALREADY-live agent and NO-OP.
        #
        # start-agent's purpose is to RECOVER a container stranded with no
        # agent.  When the "agent" tmux session ALREADY holds a live claude
        # at the input-ready marker, there is nothing to recover: re-running
        # the agent-start sequence would (a) start a SECOND
        # `agent-vault run -- claude` in the same session and (b) block on
        # the readiness-marker probe until it times out, since a session
        # already past input-ready never re-presents the trust banner.  So
        # short-circuit BEFORE the agent-start sequence: report the agent is
        # already live and online, exit zero, and DO NOT touch the session
        # (no readiness probe, no second claude).
        if self._agent_online(container):
            return CommandResult(
                exit_code=0,
                stdout=(
                    f"{container} already has a live agent and is online; "
                    "start-agent is a no-op (no readiness probe run, no "
                    "second claude agent started)\n"
                ),
            )

        out_lines: list[str] = []
        err_lines: list[str] = []

        # Resolve the messaging DSN for the readiness barrier: explicit arg >
        # SHOPMSG_DSN process env.  start-agent recovers a container that was
        # launched with its DSN already baked into the container env, so the
        # readiness barrier is best driven from the SAME source the operator
        # used at launch (an explicit --shopmsg-dsn, or the SHOPMSG_DSN env).
        dsn = shopmsg_dsn or os.environ.get(SHOPMSG_DSN_ENV)

        # Resolve the probe broker address the same way launch does: an
        # explicit broker wins, else derive from the resolved product slug
        # (manifest product > SHOPMSG_SYSTEM_SLUG env > default).
        explicit_broker = (
            agent_vault_broker
            or os.environ.get("BCLAUNCHER_AGENT_VAULT_BROKER")
        )
        manifest_product: str | None = None
        try:
            manifest_product = _read_product_from_manifest(
                manifest_path or Path("bc-manifest.yaml")
            )
        except ManifestProductTypeError:
            manifest_product = None
        if env_system_slug := os.environ.get(SHOPMSG_SYSTEM_SLUG_ENV):
            resolved_system_slug = env_system_slug
        elif manifest_product:
            resolved_system_slug = manifest_product
        else:
            resolved_system_slug = DEFAULT_SYSTEM_SLUG
        probe_broker_address = resolve_probe_broker_address(
            explicit_broker, resolved_system_slug
        )

        out_lines.append(
            f"Recovering agent in already-cloned container {container} "
            "(no re-clone)\n"
        )
        return self._start_agent_session(
            bc_name,
            container,
            startup_prompt,
            dsn,
            probe_broker_address,
            out_lines,
            err_lines,
        )
