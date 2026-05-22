"""
Business logic for bc-container subcommands.

All Docker interaction goes through the DockerDriver interface, making this
layer fully testable without a live Docker daemon.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass

from bc_launcher.driver import ContainerMount, DockerDriver


# ---------------------------------------------------------------------------
# Constants / defaults
# ---------------------------------------------------------------------------

CONTAINER_WORKSPACE = "/workspace"
AGENT_TMUX_SESSION = "agent"
BC_IMAGE = "ghcr.io/shopsystem/bc-base:latest"
SHOPMSG_DSN_ENV = "SHOPMSG_DSN"


def _container_name(bc_name: str) -> str:
    return f"bc-{bc_name}"


# ---------------------------------------------------------------------------
# Result type returned by commands that produce output
# ---------------------------------------------------------------------------

@dataclass
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str = ""


# ---------------------------------------------------------------------------
# Subcommand implementations
# ---------------------------------------------------------------------------

class BcContainerController:
    """
    Pure-Python controller for bc-container operations.

    Accepts a DockerDriver at construction time so tests can inject fakes.
    """

    def __init__(self, driver: DockerDriver) -> None:
        self._driver = driver

    # ------------------------------------------------------------------
    # launch
    # ------------------------------------------------------------------

    def launch(
        self,
        bc_name: str,
        repo_url: str | None = None,
        shopmsg_dsn: str | None = None,
        startup_prompt: str | None = None,
        network: str | None = None,
    ) -> CommandResult:
        """
        Start a Docker container for the named BC.

        Idempotent: if the container is already running, report and exit 0.
        """
        container = _container_name(bc_name)

        if self._driver.is_running(container):
            return CommandResult(
                exit_code=0,
                stdout=f"{container} is already running\n",
            )

        # Build environment
        env: dict[str, str] = {}
        if shopmsg_dsn:
            env[SHOPMSG_DSN_ENV] = shopmsg_dsn
        elif dsn := os.environ.get(SHOPMSG_DSN_ENV):
            env[SHOPMSG_DSN_ENV] = dsn

        # Mounts: only the BC's own workspace mount + optional SHOPMSG socket
        mounts: list[tuple[str, str, str]] = []

        # SHOPMSG_DSN may be a postgres DSN (no socket mount needed) or a
        # unix socket path.  If the DSN value looks like a socket file, add a
        # bind mount for it.
        dsn_value = env.get(SHOPMSG_DSN_ENV, "")
        if dsn_value.startswith("/") and not dsn_value.startswith("//"):
            # It's a host socket path — mount the containing directory
            socket_dir = os.path.dirname(dsn_value)
            mounts.append(("bind", socket_dir, socket_dir))

        self._driver.run(
            container_name=container,
            image=BC_IMAGE,
            env=env,
            mounts=mounts,
            network=network,
            detach=True,
        )

        out_lines: list[str] = [f"Started container {container}\n"]

        # Clone repository if URL provided
        if repo_url:
            clone_result = self._driver.exec_run(
                container,
                ["git", "clone", repo_url, CONTAINER_WORKSPACE],
            )
            if clone_result.returncode != 0:
                return CommandResult(
                    exit_code=1,
                    stdout="".join(out_lines),
                    stderr=f"git clone failed: {clone_result.stderr}",
                )
            out_lines.append(f"Cloned {repo_url} into {CONTAINER_WORKSPACE}\n")

            # bd dolt pull
            bd_result = self._driver.exec_run(
                container,
                ["bd", "dolt", "pull"],
            )
            out_lines.append("Ran bd dolt pull\n")

        # Start tmux session
        tmux_result = self._driver.exec_run(
            container,
            ["tmux", "new-session", "-d", "-s", AGENT_TMUX_SESSION],
        )
        out_lines.append(f"Started tmux session '{AGENT_TMUX_SESSION}'\n")

        # Optional startup prompt
        if startup_prompt:
            self._driver.exec_run(
                container,
                ["tmux", "send-keys", "-t", AGENT_TMUX_SESSION,
                 startup_prompt, "Enter"],
            )
            out_lines.append(f"Injected startup prompt: {startup_prompt!r}\n")

        return CommandResult(exit_code=0, stdout="".join(out_lines))

    # ------------------------------------------------------------------
    # attach
    # ------------------------------------------------------------------

    def attach(self, bc_name: str) -> None:
        """
        Attach to the BC container's tmux session interactively.
        Replaces the current process via exec.
        """
        container = _container_name(bc_name)
        self._driver.exec_interactive(
            container,
            ["tmux", "attach-session", "-t", AGENT_TMUX_SESSION],
        )

    # ------------------------------------------------------------------
    # inject
    # ------------------------------------------------------------------

    def inject(self, bc_name: str, prompt_text: str) -> CommandResult:
        """Send text to the container's tmux session."""
        container = _container_name(bc_name)
        self._driver.exec_run(
            container,
            ["tmux", "send-keys", "-t", AGENT_TMUX_SESSION, prompt_text, "Enter"],
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
        result = self._driver.exec_run(
            container,
            ["tmux", "capture-pane", "-p", "-t", AGENT_TMUX_SESSION],
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
        """Report running state of the BC container and its tmux session."""
        container = _container_name(bc_name)
        is_running = self._driver.is_running(container)

        if not is_running:
            return CommandResult(
                exit_code=0,
                stdout=(
                    f"bc_name: {bc_name}\n"
                    f"container: {container}\n"
                    f"container_state: stopped\n"
                ),
            )

        # Check tmux session
        tmux_result = self._driver.exec_run(
            container,
            ["tmux", "has-session", "-t", AGENT_TMUX_SESSION],
        )
        tmux_state = "active" if tmux_result.returncode == 0 else "inactive"

        return CommandResult(
            exit_code=0,
            stdout=(
                f"bc_name: {bc_name}\n"
                f"container: {container}\n"
                f"container_state: running\n"
                f"tmux_session: {tmux_state}\n"
            ),
        )

    # ------------------------------------------------------------------
    # list
    # ------------------------------------------------------------------

    def list_containers(self) -> CommandResult:
        """List all known BC containers with their states."""
        infos = self._driver.list_bc_containers()
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
