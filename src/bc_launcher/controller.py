"""
Business logic for bc-container subcommands.

All Docker interaction goes through the DockerDriver interface, making this
layer fully testable without a live Docker daemon.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

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


def _slugify(text: str) -> str:
    """Lowercase and replace runs of spaces with hyphens."""
    return re.sub(r"\s+", "-", text.strip().lower())


def _read_product_from_manifest(manifest_path: Path) -> str | None:
    """Read the 'product' field from a bc-manifest.yaml file.

    Returns None if the file does not exist or has no 'product' key.
    Raises yaml.YAMLError on parse failure.
    """
    import yaml
    if not manifest_path.exists():
        return None
    data = yaml.safe_load(manifest_path.read_text())
    if isinstance(data, dict):
        return data.get("product")
    return None


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
        manifest_path: Path | None = None,
        credential_home: Path | None = None,
    ) -> CommandResult:
        """
        Start a Docker container for the named BC.

        Idempotent: if the container is already running, report and exit 0.

        Network resolution (in priority order):
        1. If ``network`` is provided explicitly, use it as-is (no auto-create).
        2. Otherwise, read ``product:`` from bc-manifest.yaml (at ``manifest_path``
           or ``Path("bc-manifest.yaml")`` in CWD), slugify it, and use that as the
           network name.  If the network does not yet exist, create it first.
        3. If neither source is available, return a non-zero error.

        Credential propagation:
        The following host paths are bind-mounted into the container so that
        Claude and GitHub credentials are available to the agent:
          - $HOME/.claude       → /home/vscode/.claude        (read-write)
          - $HOME/.config/gh    → /home/vscode/.config/gh     (read-write)
          - $HOME/.gitconfig    → /tmp/host-gitconfig          (read-only)
        After container start, the controller copies:
          - /tmp/host-gitconfig → /home/vscode/.gitconfig
          - /home/vscode/.claude/.claude.json → /home/vscode/.claude.json
        All three host paths must exist; if any is missing the launch fails fast.
        ``credential_home`` overrides the home directory used for these paths
        (useful in tests).
        """
        container = _container_name(bc_name)

        if self._driver.is_running(container):
            return CommandResult(
                exit_code=0,
                stdout=f"{container} is already running\n",
            )

        # --- Network resolution ---
        resolved_network: str | None = network
        auto_create_network = False

        if resolved_network is None:
            # Try to derive from manifest
            effective_manifest = manifest_path or Path("bc-manifest.yaml")
            product = _read_product_from_manifest(effective_manifest)
            if product:
                resolved_network = _slugify(product)
                auto_create_network = True
            else:
                return CommandResult(
                    exit_code=1,
                    stdout="",
                    stderr="no network: bc-manifest.yaml not found and --network not provided\n",
                )

        # Create the derived network if it does not yet exist (only for auto-derived, not explicit)
        if auto_create_network and not self._driver.network_exists(resolved_network):
            self._driver.network_create(resolved_network)

        # --- Credential path resolution ---
        home = credential_home if credential_home is not None else Path.home()
        claude_dir = home / ".claude"
        gh_config_dir = home / ".config" / "gh"
        gitconfig_file = home / ".gitconfig"

        # Fail fast if any default credential source is missing
        for host_path, display_name in [
            (claude_dir, "$HOME/.claude"),
            (gh_config_dir, "$HOME/.config/gh"),
            (gitconfig_file, "$HOME/.gitconfig"),
        ]:
            if not host_path.exists():
                return CommandResult(
                    exit_code=1,
                    stdout="",
                    stderr=f"credential source not found: {display_name}\n",
                )

        # Build environment
        env: dict[str, str] = {}
        if shopmsg_dsn:
            env[SHOPMSG_DSN_ENV] = shopmsg_dsn
        elif dsn := os.environ.get(SHOPMSG_DSN_ENV):
            env[SHOPMSG_DSN_ENV] = dsn

        # Mounts: credential mounts + optional SHOPMSG socket
        # Each entry: (type, source, dest, readonly)
        mounts: list[tuple[str, str, str, bool]] = []

        # Credential bind mounts
        mounts.append(("bind", str(claude_dir), "/home/vscode/.claude", False))
        mounts.append(("bind", str(gh_config_dir), "/home/vscode/.config/gh", False))
        mounts.append(("bind", str(gitconfig_file), "/tmp/host-gitconfig", True))

        # SHOPMSG_DSN may be a postgres DSN (no socket mount needed) or a
        # unix socket path.  If the DSN value looks like a socket file, add a
        # bind mount for it.
        dsn_value = env.get(SHOPMSG_DSN_ENV, "")
        if dsn_value.startswith("/") and not dsn_value.startswith("//"):
            # It's a host socket path — mount the containing directory
            socket_dir = os.path.dirname(dsn_value)
            mounts.append(("bind", socket_dir, socket_dir, False))

        self._driver.run(
            container_name=container,
            image=BC_IMAGE,
            env=env,
            mounts=mounts,
            network=resolved_network,
            detach=True,
        )

        out_lines: list[str] = [f"Started container {container}\n"]

        # Copy staged gitconfig into container user's home
        self._driver.exec_run(
            container,
            ["cp", "/tmp/host-gitconfig", "/home/vscode/.gitconfig"],
        )
        out_lines.append("Copied host gitconfig into container\n")

        # Copy .claude.json from mounted ~/.claude into container user's home root
        self._driver.exec_run(
            container,
            ["cp", "/home/vscode/.claude/.claude.json", "/home/vscode/.claude.json"],
        )
        out_lines.append("Copied .claude.json into container home\n")

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
