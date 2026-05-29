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
BC_IMAGE = "ghcr.io/dstengle/shopsystem-bc-base:latest"
SHOPMSG_DSN_ENV = "SHOPMSG_DSN"

# Claude Code readiness markers used to sequence prompt injection inside
# the agent tmux session.  The default tmux session command is bash, so a
# naïve send-keys of the startup prompt lands in bash and fails as
# "-bash: <first-word>: command not found".  The launch sequence is:
#   1. send-keys 'claude --dangerously-skip-permissions' Enter
#                                         — start Claude Code with
#                                           in-container permission bypass
#                                           (the BC container is the
#                                           isolation boundary)
#   2. wait for CLAUDE_READY_MARKER       — workspace-trust banner appeared
#                                           (PRE-trust: this is the line
#                                           Claude Code prints BEFORE the
#                                           trust prompt clears, so it
#                                           confirms the agent has reached
#                                           interactive UI without
#                                           presupposing trust was accepted)
#   3. send-keys Enter                    — accept workspace-trust default
#                                           (empirically verified that
#                                           --dangerously-skip-permissions
#                                           does NOT bypass workspace trust,
#                                           so this step is still required)
#   4. wait for CLAUDE_INPUT_READY_MARKER — main input prompt is live
#                                           (POST-trust: "bypass permissions
#                                           on" appears only once the trust
#                                           prompt has cleared and the
#                                           main input UI is live — chosen
#                                           in preference to the bare "❯"
#                                           glyph because the PRE-trust
#                                           pane also contains "❯" as the
#                                           trust-prompt selector arrow,
#                                           which would otherwise cause
#                                           step 4 to succeed trivially)
#   5. send-keys <startup_prompt> Enter   — prompt lands inside Claude Code
# On any wait timeout, the launcher emits a stderr warning naming the
# step that did not confirm.
CLAUDE_READY_MARKER = "Accessing workspace:"
CLAUDE_INPUT_READY_MARKER = "bypass permissions on"
CLAUDE_READINESS_TIMEOUT_SECONDS = 60.0


def _container_name(bc_name: str) -> str:
    return f"bc-{bc_name}"


def _slugify(text: str) -> str:
    """Lowercase and replace runs of spaces with hyphens."""
    return re.sub(r"\s+", "-", text.strip().lower())


def _resolve_host_path(devcontainer_path: Path) -> Path:
    """
    If running inside a devcontainer where ``devcontainer_path`` lies on a bind
    mount, return the corresponding host-visible source path.  Falls back to
    ``devcontainer_path`` if no covering bind mount is found (i.e., not inside
    a bind-mounted devcontainer).

    Needed because mount sources passed to ``docker run`` are interpreted by
    the host docker daemon — bind-mount sources like ``/home/vscode/.claude``
    that are valid inside the launching container may not exist on the host.

    Resolution order:
      1. If ``BCLAUNCHER_HOST_HOME`` env var is set and the path is under the
         current ``Path.home()``, substitute the env var for the home prefix.
         This handles devcontainers whose home is bind-mounted from a host
         user home that we know explicitly.
      2. Otherwise walk ``/proc/self/mountinfo`` for the longest mount-point
         prefix that covers the path, and substitute the source root.
      3. Otherwise return the path unchanged.
    """
    try:
        target = str(devcontainer_path.resolve())
    except OSError:
        target = str(devcontainer_path)
    host_home = os.environ.get("BCLAUNCHER_HOST_HOME")
    if host_home:
        home_str = str(Path.home())
        if target == home_str:
            return Path(host_home)
        if target.startswith(home_str + "/"):
            return Path(host_home + target[len(home_str):])
    best_mount_point: str | None = None
    best_source_root: str | None = None
    try:
        with open("/proc/self/mountinfo", encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 5:
                    continue
                source_root = parts[3]
                mount_point = parts[4]
                if target == mount_point or target.startswith(mount_point + "/"):
                    if best_mount_point is None or len(mount_point) > len(best_mount_point):
                        best_mount_point = mount_point
                        best_source_root = source_root
    except OSError:
        return devcontainer_path
    if best_mount_point is None or best_source_root is None:
        return devcontainer_path
    if target == best_mount_point:
        resolved = best_source_root
    else:
        suffix = target[len(best_mount_point):]
        resolved = best_source_root + suffix
    # mountinfo source roots may be dataset-relative (start with "/<user>/...")
    # rather than absolute host paths.  When BCLAUNCHER_HOST_HOME is set, apply
    # the same home-prefix substitution to the mountinfo result so it lands at
    # an absolute host path.
    if host_home:
        user_leaf = "/" + Path(host_home).name
        if resolved == user_leaf:
            return Path(host_home)
        if resolved.startswith(user_leaf + "/"):
            return Path(host_home + resolved[len(user_leaf):])
    return Path(resolved)


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
        # shopsystem-devcontainer bind-mounts the host's gitconfig at
        # /tmp/host-gitconfig; prefer that when present so we have a path the
        # host docker daemon can resolve via /proc/self/mountinfo translation.
        _staged_gitconfig = Path("/tmp/host-gitconfig")
        if credential_home is None and _staged_gitconfig.is_file():
            gitconfig_file = _staged_gitconfig
        else:
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
        # The BC image runs as root by default, but credentials live under
        # /home/vscode/.  Point HOME at vscode's home so gh / git find their
        # configs without permission games.
        env["HOME"] = "/home/vscode"
        if shopmsg_dsn:
            env[SHOPMSG_DSN_ENV] = shopmsg_dsn
        elif dsn := os.environ.get(SHOPMSG_DSN_ENV):
            env[SHOPMSG_DSN_ENV] = dsn

        # Mounts: credential mounts + optional SHOPMSG socket
        # Each entry: (type, source, dest, readonly)
        mounts: list[tuple[str, str, str, bool]] = []

        # Credential bind mounts — translate to host paths when running inside
        # a devcontainer (the host docker daemon needs host-visible sources).
        mounts.append(("bind", str(_resolve_host_path(claude_dir)), "/home/vscode/.claude", False))
        mounts.append(("bind", str(_resolve_host_path(gh_config_dir)), "/home/vscode/.config/gh", False))
        mounts.append(("bind", str(_resolve_host_path(gitconfig_file)), "/tmp/host-gitconfig", True))

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
        err_lines: list[str] = []

        # Copy staged gitconfig into container user's home
        self._driver.exec_run(
            container,
            ["cp", "/tmp/host-gitconfig", "/home/vscode/.gitconfig"],
        )
        out_lines.append("Copied host gitconfig into container\n")

        # Copy .claude.json from mounted ~/.claude into container user's home root,
        # but only if the host file exists.  Missing .claude.json is non-fatal:
        # warn to stderr and proceed (brief 007 minimum-friction posture).
        claude_json_file = home / ".claude" / ".claude.json"
        if claude_json_file.exists():
            self._driver.exec_run(
                container,
                ["cp", "/home/vscode/.claude/.claude.json", "/home/vscode/.claude.json"],
            )
            out_lines.append("Copied .claude.json into container home\n")
        else:
            err_lines.append(
                f"warning: .claude.json not found; skipping copy into container\n"
            )

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
            # Step 1: start Claude Code with --dangerously-skip-permissions.
            # The BC container is the isolation boundary the permission
            # prompts are meant to substitute for; bypassing them inside
            # this container prevents the agent from hanging on permission
            # gates that have no operator at the other end.
            self._driver.exec_run(
                container,
                ["tmux", "send-keys", "-t", AGENT_TMUX_SESSION,
                 "claude --dangerously-skip-permissions", "Enter"],
            )
            # Step 2: wait for the PRE-trust workspace-trust banner.
            # CLAUDE_READY_MARKER is "Accessing workspace:" — the first
            # claude-output line that appears after invocation, BEFORE
            # trust is accepted.  (The earlier "Claude Code v" marker was
            # the POST-trust banner and produced the chicken-and-egg
            # deadlock that this fix addresses.)
            ready = self._driver.wait_for_pane_marker(
                container,
                AGENT_TMUX_SESSION,
                CLAUDE_READY_MARKER,
                CLAUDE_READINESS_TIMEOUT_SECONDS,
            )
            if not ready:
                err_lines.append(
                    f"warning: Claude Code did not become ready within "
                    f"{CLAUDE_READINESS_TIMEOUT_SECONDS:.0f}s "
                    f"(marker {CLAUDE_READY_MARKER!r} not seen); "
                    f"startup prompt NOT injected\n"
                )
                return CommandResult(
                    exit_code=0,
                    stdout="".join(out_lines),
                    stderr="".join(err_lines),
                )
            # Step 3: accept workspace-trust prompt (default "Yes, I trust").
            # Empirically verified (2026-05-29) that
            # --dangerously-skip-permissions does NOT bypass workspace trust:
            # `claude --dangerously-skip-permissions` in a fresh directory
            # still presents the "Quick safety check" / "Yes, I trust this
            # folder" prompt.  So this Enter is still required to advance
            # to the main input UI; it now correctly fires AFTER a PRE-trust
            # marker (step 2) rather than after a POST-trust banner.
            self._driver.exec_run(
                container,
                ["tmux", "send-keys", "-t", AGENT_TMUX_SESSION, "Enter"],
            )
            # Step 4: wait for the POST-trust input-ready marker.
            # CLAUDE_INPUT_READY_MARKER is "bypass permissions on" — only
            # present once the trust prompt has cleared AND
            # --dangerously-skip-permissions is active, which is the exact
            # state in which the user prompt can be safely injected.
            input_ready = self._driver.wait_for_pane_marker(
                container,
                AGENT_TMUX_SESSION,
                CLAUDE_INPUT_READY_MARKER,
                CLAUDE_READINESS_TIMEOUT_SECONDS,
            )
            if not input_ready:
                err_lines.append(
                    f"warning: Claude Code workspace-trust prompt did not "
                    f"clear / main input did not become ready within "
                    f"{CLAUDE_READINESS_TIMEOUT_SECONDS:.0f}s "
                    f"(marker {CLAUDE_INPUT_READY_MARKER!r} not seen); "
                    f"startup prompt NOT injected\n"
                )
                return CommandResult(
                    exit_code=0,
                    stdout="".join(out_lines),
                    stderr="".join(err_lines),
                )
            # Step 5: inject the startup prompt into Claude Code's input
            self._driver.exec_run(
                container,
                ["tmux", "send-keys", "-t", AGENT_TMUX_SESSION,
                 startup_prompt, "Enter"],
            )
            out_lines.append(f"Injected startup prompt: {startup_prompt!r}\n")

        return CommandResult(exit_code=0, stdout="".join(out_lines), stderr="".join(err_lines))

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
