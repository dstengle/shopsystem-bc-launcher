"""EngageMixin for BcContainerController: fabro engage, launch-diagnostic
write, and the start_agent recovery entrypoint. Part of the controller.py
decomposition; combined into the single class in core.py via self.
"""
from __future__ import annotations
import os
from pathlib import Path

from bc_launcher.constants import (
    AGENT_TMUX_SESSION,
    AGENT_CONTAINER_USER,
    SHOPMSG_DSN_ENV,
)
from bc_launcher.controller._result import (
    CommandResult,
)
from bc_launcher.diagnostics import (
    CAUSE_MARKER_AGENT_VAULT,
    CAUSE_MARKER_MESSAGING_DB,
    launch_diagnostic_path,
)
from bc_launcher.fabro import (
    _fabro_exec_env,
    FABRO_DISPATCHER_FILE,
    FABRO_WORKFLOW_FILE,
    _fabro_engage_script,
    _fabro_server_start_argv,
)
from bc_launcher.manifest import (
    ManifestProductTypeError,
    _read_product_from_manifest,
)
from bc_launcher.readiness import (
    ESCAPE_AFFORDANCE_MARKER,
    ESCAPE_KEY_NAME,
    OPTION_SCREEN_MARKER,
)
from bc_launcher.naming import (
    _container_name,
)
from bc_launcher.networking import (
    DEFAULT_SYSTEM_SLUG,
    SHOPMSG_SYSTEM_SLUG_ENV,
    resolve_probe_broker_address,
)


class EngageMixin:


    def _fabro_engage(
        self,
        bc_name: str,
        container: str,
        dsn: str | None,
        probe_broker_address: str,
        work_id: str | None,
        out_lines: list[str],
        err_lines: list[str],
        provider: str | None = None,
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
            ["/bin/sh", "-c", _fabro_engage_script(bc_name, provider=provider)],
            user=AGENT_CONTAINER_USER,
            env=_fabro_exec_env(),
            detach=True,
        )
        if engage_result.returncode != 0:
            reason = (
                f"fabro engage failure: the external watcher supervisor "
                f"(`fabro server start` + `shop-msg watch` driving finite "
                f"`fabro run {FABRO_WORKFLOW_FILE}` children) exited "
                f"{engage_result.returncode}: "
                f"{(engage_result.stderr or engage_result.stdout).strip()}"
            )
            err_lines.append("warning: " + reason + "\n")
            return CommandResult(
                exit_code=1,
                stdout="".join(out_lines),
                stderr="".join(err_lines),
            )
        out_lines.append(
            "Fabro orchestrator engage (lead-1vbw / ADR-058 AMENDMENT-3): "
            "started EXACTLY ONE long-lived per-container fabro server "
            f"({' '.join(_fabro_server_start_argv())}) and engaged the EXTERNAL "
            "agent-free message-driven watcher supervisor (always-resident "
            "`shop-msg watch` = bc_presence heartbeat; each inbound message "
            f"fires ONE finite `fabro run {FABRO_WORKFLOW_FILE}` child against "
            "the one shared server; startup drain + in-flight dedup + telemetry "
            "surface); no tmux 'agent' send-keys session and no 'claude' engage "
            "started on this path — the engage tier is REPLACED by the fabro "
            "watcher run-graph entry (ADR-050 D3)\n"
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

    def _handle_option_screen(
        self, container: str, out_lines: list[str], err_lines: list[str]
    ) -> CommandResult | None:
        """Handle a blocking interactive option screen before prompt inject
        (lead-q3uy): Esc-dismiss an escapable screen, or refuse (return a
        CommandResult) an un-escapable one. Returns None to continue. Extracted
        from _start_agent_session verbatim."""
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
