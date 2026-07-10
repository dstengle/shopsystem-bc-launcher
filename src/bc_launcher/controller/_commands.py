"""CommandsMixin for BcContainerController (controller.py decomposition, Phase 2).

Split from the former monolithic BcContainerController. Combined back into
the single public class in bc_launcher.controller.core; methods call each
other through ``self`` exactly as before.
"""
from __future__ import annotations
import os

from bc_launcher.constants import (
    AGENT_CONTAINER_USER,
    AGENT_TMUX_SESSION,
    CONTAINER_WORKSPACE,
    SHOPMSG_DSN_ENV,
)
from bc_launcher.controller._result import (
    CommandResult,
)
from bc_launcher.driver import (
    ContainerMount,
    DockerSocketUnreachableError,
)
from bc_launcher.naming import (
    _container_name,
)


class CommandsMixin:

    # ------------------------------------------------------------------
    # attach
    # ------------------------------------------------------------------

    def attach(self, bc_name: str) -> None:
        """
        Attach to the BC container's tmux session interactively.
        Replaces the current process via exec.
        """
        container = _container_name(bc_name)
        # Attach as vscode: the agent tmux session is owned by vscode (see
        # launch()), and tmux refuses cross-user attach.
        self._driver.exec_interactive(
            container,
            ["tmux", "attach-session", "-t", AGENT_TMUX_SESSION],
            user=AGENT_CONTAINER_USER,
        )


    # ------------------------------------------------------------------
    # inject
    # ------------------------------------------------------------------

    def inject(self, bc_name: str, prompt_text: str) -> CommandResult:
        """Send text to the container's tmux session."""
        container = _container_name(bc_name)
        # send-keys against the vscode-owned tmux server must run as vscode.
        #
        # Two DISCRETE send-keys invocations (text first, Enter second), NOT
        # one invocation carrying both (lead-lez1 / lead-9q0f root cause).  A
        # single `send-keys <text> Enter` exec_run concatenates the whole
        # keystream into ONE pty write() syscall; Claude Code's TUI treats
        # single-write payloads above ~70 bytes as a paste and absorbs the
        # trailing CR into the input buffer instead of submitting.  Two
        # exec_run calls are two discrete pty writes separated by a
        # kernel-scheduling gap, which the TUI processes as a discrete submit.
        self._driver.exec_run(
            container,
            ["tmux", "send-keys", "-t", AGENT_TMUX_SESSION, prompt_text],
            user=AGENT_CONTAINER_USER,
        )
        self._driver.exec_run(
            container,
            ["tmux", "send-keys", "-t", AGENT_TMUX_SESSION, "Enter"],
            user=AGENT_CONTAINER_USER,
        )
        return CommandResult(
            exit_code=0,
            stdout=f"Sent {prompt_text!r} to {AGENT_TMUX_SESSION} in {container}\n",
        )


    # ------------------------------------------------------------------
    # monitor
    # ------------------------------------------------------------------

    def monitor(self, bc_name: str) -> CommandResult:
        """Capture and return the current contents of the tmux session pane."""
        container = _container_name(bc_name)
        # capture-pane against the vscode-owned tmux server must run as vscode.
        result = self._driver.exec_run(
            container,
            ["tmux", "capture-pane", "-p", "-t", AGENT_TMUX_SESSION],
            user=AGENT_CONTAINER_USER,
        )
        return CommandResult(
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )


    # ------------------------------------------------------------------
    # stop
    # ------------------------------------------------------------------

    def stop(self, bc_name: str) -> CommandResult:
        """Stop and remove the BC container."""
        container = _container_name(bc_name)
        self._driver.stop(container)
        return CommandResult(exit_code=0, stdout=f"Stopped {container}\n")


    # ------------------------------------------------------------------
    # status
    # ------------------------------------------------------------------

    def status(self, bc_name: str) -> CommandResult:
        """Report running state of the BC container and its tmux session.

        lead-wdvx (Bug 2): ``status`` is docker-dependent — its first act
        probes docker for the container's running state.  When that probe
        fails because the docker socket is unreachable (daemon down, OR a
        CONFIG fault: socket mounted-but-permission-denied, or not mounted),
        the driver raises ``DockerSocketUnreachableError``.  We surface that
        as a NON-ZERO exit with a stderr line NAMING the cause, distinct from
        the legitimate "container_state: stopped" an absent container reports
        at exit 0 — so a docker config fault is never masked as a (false)
        absent/stopped result.
        """
        container = _container_name(bc_name)
        try:
            is_running = self._driver.is_running(container)
        except DockerSocketUnreachableError as exc:
            detail = str(exc).strip()
            stderr = (
                "bc-container status: the Docker socket could not be reached "
                "(the Docker daemon is unreachable); cannot determine "
                "container state"
            )
            if detail:
                stderr += f": {detail}"
            return CommandResult(exit_code=1, stdout="", stderr=stderr + "\n")

        if not is_running:
            return CommandResult(
                exit_code=0,
                stdout=(
                    f"bc_name: {bc_name}\n"
                    f"container: {container}\n"
                    f"container_state: stopped\n"
                ),
            )

        # Check tmux session.  has-session against the vscode-owned tmux
        # server must run as vscode, same as every other tmux client call.
        tmux_result = self._driver.exec_run(
            container,
            ["tmux", "has-session", "-t", AGENT_TMUX_SESSION],
            user=AGENT_CONTAINER_USER,
        )
        tmux_state = "active" if tmux_result.returncode == 0 else "inactive"

        # Agent presence (lead-pixf / f2ddd6c7).  The tmux "agent" session
        # being merely present ("active") does NOT by itself mean an agent
        # is actually doing work: an empty session left at a bash prompt is
        # "active" but offline.  An agent is ONLINE only when the "agent"
        # tmux session holds a LIVE claude process whose `shop-msg watch`
        # inbox watcher is armed — that is the state in which the BC is
        # actually reachable for dispatched work.  Anything short of that
        # (no session, a session with no live claude, or a claude whose
        # watcher is not armed) is reported as "offline".
        agent_presence = (
            "online" if self._agent_online(container) else "offline"
        )

        return CommandResult(
            exit_code=0,
            stdout=(
                f"bc_name: {bc_name}\n"
                f"container: {container}\n"
                f"container_state: running\n"
                f"tmux_session: {tmux_state}\n"
                f"agent_presence: {agent_presence}\n"
            ),
        )


    # ------------------------------------------------------------------
    # agent presence (lead-pixf) — shared by status + start-agent
    # ------------------------------------------------------------------

    def _agent_online(self, container: str) -> bool:
        """Return True when the container's "agent" tmux session holds a LIVE
        claude process whose ``shop-msg watch`` inbox watcher is armed.

        lead-pixf.  This is the agent-presence determinant for the ``status``
        report (f2ddd6c7) and the no-op short-circuit for ``start-agent``
        (aeebb281).  It is delegated to the driver so the real driver can
        probe the live in-container process table / watcher state while the
        fake can model a live-agent container directly.  When the driver does
        not expose the probe (older driver), presence resolves to False so the
        command degrades to "offline" rather than crashing.
        """
        probe = getattr(self._driver, "agent_online", None)
        return bool(probe(container)) if callable(probe) else False


    # ------------------------------------------------------------------
    # readiness sequence (messaging-DB barrier, idempotent)
    # ------------------------------------------------------------------

    def ensure_ready(
        self,
        bc_name: str,
        shopmsg_dsn: str | None = None,
    ) -> CommandResult:
        """Run (or re-run) the messaging readiness sequence for the container.

        Idempotent: re-running against a container that has already passed
        its readiness sequence is a no-op that exits zero and reports the
        container is already ready — it does NOT re-send any startup prompt.

        Returns non-zero with a DSN-naming stderr line when the messaging
        database is unreachable.
        """
        container = _container_name(bc_name)
        dsn = shopmsg_dsn or os.environ.get(SHOPMSG_DSN_ENV)

        if self._container_marked_ready(container):
            return CommandResult(
                exit_code=0,
                stdout=f"{container} is already ready\n",
            )

        if dsn and not self._driver.messaging_db_reachable(dsn):
            return CommandResult(
                exit_code=1,
                stdout="",
                stderr=(
                    f"messaging readiness failure: messaging database at "
                    f"{SHOPMSG_DSN_ENV}={dsn} is not reachable\n"
                ),
            )

        self._mark_container_ready(container)
        return CommandResult(
            exit_code=0,
            stdout=f"{container} is ready\n",
        )


    # lead-q5k7 — derive the shop-type for `shop-templates update
    # --shop-type <bc|lead>` from the cloned shop's own canonical marker
    # file `.claude/shop/type.md` (contents "bc" or "lead").  The update
    # MUST use the type the shop was originally bootstrapped with, and the
    # cloned repo carries that marker, so this is the authoritative source.
    # Defaults to "bc" when the marker is absent/unreadable/unrecognised so
    # the refresh still runs with the dominant shop type rather than
    # crashing the launch.
    def _read_shop_type(self, container: str) -> str:
        result = self._driver.exec_run(
            container,
            ["cat", f"{CONTAINER_WORKSPACE}/.claude/shop/type.md"],
        )
        if result.returncode == 0:
            value = (result.stdout or "").strip().lower()
            if value in ("bc", "lead"):
                return value
        return "bc"


    # Readiness bookkeeping is delegated to the driver so the fake can model
    # an already-ready container.  Real drivers may persist this as a
    # container label / sentinel; the fake holds it in memory.
    def _container_marked_ready(self, container: str) -> bool:
        marker = getattr(self._driver, "is_marked_ready", None)
        return bool(marker(container)) if callable(marker) else False


    def _mark_container_ready(self, container: str) -> None:
        marker = getattr(self._driver, "mark_ready", None)
        if callable(marker):
            marker(container)


    # ------------------------------------------------------------------
    # health
    # ------------------------------------------------------------------

    def health(self, bc_name: str) -> CommandResult:
        """Report the BC container's Docker health status.

        The container is healthy only when beads is functionally usable
        inside it AND the messaging database at SHOPMSG_DSN is reachable;
        otherwise it is unhealthy even if the agent process is alive.  This
        mirrors the in-container healthcheck the launch wires up; the host
        reads the resulting status via ``docker inspect``.
        """
        container = _container_name(bc_name)
        status = self._driver.health_status(container)
        return CommandResult(
            exit_code=0 if status == "healthy" else 1,
            stdout=f"{status}\n",
        )


    # ------------------------------------------------------------------
    # list
    # ------------------------------------------------------------------

    def list_containers(self) -> CommandResult:
        """List all known BC containers with their states.

        lead-pixf (010e776c): when the Docker socket is unreachable, the
        driver raises ``DockerSocketUnreachableError`` rather than returning
        an empty list.  We surface that as a NON-ZERO exit with a stderr
        line naming the docker-socket unreachability and emit NOTHING on
        stdout — in particular NOT "No BC containers found", which would
        mask an infrastructure outage as a (false) empty inventory.
        """
        try:
            infos = self._driver.list_bc_containers()
        except DockerSocketUnreachableError as exc:
            detail = str(exc).strip()
            stderr = (
                "bc-container list: the Docker socket could not be reached "
                "(the Docker daemon is unreachable); cannot enumerate BC "
                "containers"
            )
            if detail:
                stderr += f": {detail}"
            return CommandResult(exit_code=1, stdout="", stderr=stderr + "\n")
        if not infos:
            return CommandResult(exit_code=0, stdout="No BC containers found.\n")

        lines: list[str] = []
        for info in infos:
            # Derive bc_name by stripping leading "bc-"
            bc_name = info.name.removeprefix("bc-")
            state = "running" if info.running else "stopped"
            lines.append(f"{bc_name}: {state}\n")

        return CommandResult(exit_code=0, stdout="".join(lines))


    # ------------------------------------------------------------------
    # isolation check (used by tests and the isolate-check subcommand)
    # ------------------------------------------------------------------

    def get_bind_mounts(self, container_name: str) -> list[ContainerMount]:
        """Return only bind-type mounts for a running container."""
        all_mounts = self._driver.get_mounts(container_name)
        return [m for m in all_mounts if m.type == "bind"]


    def last_command(self) -> list[str]:
        return self._driver.last_command()
