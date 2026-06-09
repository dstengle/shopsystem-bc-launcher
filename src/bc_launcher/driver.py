"""
Docker driver interface and real implementation.

The DockerDriver protocol is the seam between bc_launcher business logic and
the real Docker daemon.  Tests inject a FakeDockerDriver; production code uses
RealDockerDriver which shells out to the docker CLI.
"""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from typing import Protocol


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class ContainerMount:
    """Represents one mount entry for a running container."""
    type: str          # "bind", "volume", "tmpfs", …
    source: str        # host-side source path / volume name / socket path
    destination: str   # path inside the container


@dataclass
class ContainerInfo:
    """Aggregated state for a BC container."""
    name: str
    running: bool
    mounts: list[ContainerMount] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Registry driver protocol (scenario af2f03d3ac519cb5)
# ---------------------------------------------------------------------------

class RegistryDriver(Protocol):
    """Resolve a registry image reference to its current digest.

    The seam scenario 39 (af2f03d3ac519cb5) pins: launch must resolve the
    bc-base ``latest`` tag against the registry BEFORE starting the container,
    so a republished image (a newer digest under the same ``latest`` tag)
    reaches the new container instead of a stale locally-cached digest.

    This protocol belongs to scenario 39 ONLY (the launch-path / behavioral
    side).  It is explicitly NOT shared with the CI/workflow scenarios
    37/38/41, which are pinned declaratively as committed YAML with no in-src
    seam (per the architect ruling on lead-yk3o).
    """

    def resolve_digest(self, image_ref: str) -> str:
        """Resolve ``image_ref`` (e.g. ``ghcr.io/...:latest``) to a digest.

        Returns the registry-current digest for the tag named in
        ``image_ref``.  The real implementation queries the registry; the
        test fake returns the digest the test has configured as the
        registry-current ``latest``.
        """
        ...


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

class DockerDriver(Protocol):
    """Minimal Docker operations needed by bc_launcher."""

    def is_running(self, container_name: str) -> bool:
        """Return True if the named container is currently running."""
        ...

    def run(
        self,
        container_name: str,
        image: str,
        env: dict[str, str],
        mounts: list[tuple[str, str, str, bool]],  # (type, source, dest, readonly)
        network: str | None,
        detach: bool,
    ) -> None:
        """Start a new container."""
        ...

    def exec_run(
        self,
        container_name: str,
        command: list[str],
        user: str | None = None,
    ) -> subprocess.CompletedProcess:
        """Execute a command inside a running container.

        If ``user`` is provided, run the command as that container user
        (``docker exec -u <user>``).  This is required for tmux client
        operations against an agent session owned by a non-root user:
        tmux refuses cross-user attach, so every send-keys / capture-pane
        / has-session call against a vscode-owned tmux session must also
        run as vscode.
        """
        ...

    def exec_interactive(
        self,
        container_name: str,
        command: list[str],
        user: str | None = None,
    ) -> None:
        """Execute an interactive command inside a running container (replaces process).

        ``user`` has the same semantics as in ``exec_run``: required for
        ``tmux attach-session`` against an agent session owned by a
        non-root container user.
        """
        ...

    def stop(self, container_name: str) -> None:
        """Stop and remove the named container."""
        ...

    def list_bc_containers(self) -> list[ContainerInfo]:
        """Return ContainerInfo for every container whose name starts with 'bc-'."""
        ...

    def get_mounts(self, container_name: str) -> list[ContainerMount]:
        """Return the mount list for a running container."""
        ...

    def last_command(self) -> list[str]:
        """Return the most recently built shell command (for test assertions)."""
        ...

    def network_exists(self, network_name: str) -> bool:
        """Return True if the named Docker network exists."""
        ...

    def network_create(self, network_name: str) -> None:
        """Create a Docker network with the given name."""
        ...

    def wait_for_pane_marker(
        self,
        container_name: str,
        tmux_session: str,
        marker: str,
        timeout_seconds: float,
        poll_interval_seconds: float = 0.5,
    ) -> bool:
        """
        Poll ``tmux capture-pane`` for the named session until ``marker`` is
        observed in the pane contents, or until ``timeout_seconds`` elapses.

        Returns True if the marker was observed, False on timeout.  The
        controller uses this to sequence Claude Code startup inside the tmux
        session (claude command → ready banner → trust accept → input ready)
        before injecting the first user prompt.
        """
        ...

    def messaging_db_reachable(self, dsn: str) -> bool:
        """Return True if the messaging database at ``dsn`` is reachable.

        This is the readiness barrier the launcher checks before injecting
        any startup prompt: a BC agent whose messaging backend is
        unreachable cannot arm its inbox watcher or drain pending inbox, so
        engaging it is pointless.  The controller calls this BEFORE prompt
        injection; on failure it surfaces a readiness error naming the DSN
        and does NOT send the startup prompt.
        """
        ...

    def health_status(self, container_name: str) -> str:
        """Return the container's reported health status string.

        One of ``"healthy"``, ``"unhealthy"``, ``"starting"``, or
        ``"none"`` (no healthcheck configured), mirroring
        ``docker inspect --format '{{.State.Health.Status}}'``.
        """
        ...

    def agent_vault_reachable(self, broker_address: str) -> bool:
        """Return True if the agent-vault broker at ``broker_address`` is reachable.

        Under the agent-vault credential model (ADR-026) the launcher mounts
        NO host credential into the container; the agent's Claude OAuth and
        GitHub credentials are substituted on outbound requests by an
        agent-vault broker reached through an HTTPS proxy on the shopsystem
        network.  An agent whose broker is unreachable can authenticate to
        nothing, so this is a launch-time readiness barrier alongside the
        messaging-database check: the controller calls this BEFORE engaging
        the agent and surfaces a readiness failure (naming the broker
        address) when it returns False.
        """
        ...


# ---------------------------------------------------------------------------
# Real implementation (shells out to docker CLI)
# ---------------------------------------------------------------------------

class RealDockerDriver:
    """Production DockerDriver that shells out to the docker CLI."""

    def __init__(self) -> None:
        self._last_command: list[str] = []

    def is_running(self, container_name: str) -> bool:
        result = subprocess.run(
            ["docker", "ps", "--filter", f"name=^{container_name}$",
             "--format", "{{.Names}}"],
            capture_output=True, text=True, check=False,
        )
        return container_name in result.stdout.split()

    def run(
        self,
        container_name: str,
        image: str,
        env: dict[str, str],
        mounts: list[tuple[str, str, str, bool]],
        network: str | None,
        detach: bool,
    ) -> None:
        cmd = ["docker", "run", "--name", container_name]
        if detach:
            cmd.append("-d")
        for key, val in env.items():
            cmd += ["-e", f"{key}={val}"]
        for mount_type, source, dest, readonly in mounts:
            spec = f"type={mount_type},source={source},target={dest}"
            if readonly:
                spec += ",readonly"
            cmd += ["--mount", spec]
        if network:
            cmd += ["--network", network]
        cmd.append(image)
        cmd += ["sleep", "infinity"]
        self._last_command = cmd
        subprocess.run(cmd, check=True)

    def exec_run(
        self,
        container_name: str,
        command: list[str],
        user: str | None = None,
    ) -> subprocess.CompletedProcess:
        cmd = ["docker", "exec"]
        if user is not None:
            cmd += ["-u", user]
        cmd += [container_name] + command
        self._last_command = cmd
        return subprocess.run(cmd, capture_output=True, text=True, check=False)

    def exec_interactive(
        self,
        container_name: str,
        command: list[str],
        user: str | None = None,
    ) -> None:
        import os
        cmd = ["docker", "exec", "-it"]
        if user is not None:
            cmd += ["-u", user]
        cmd += [container_name] + command
        self._last_command = cmd
        os.execvp("docker", cmd)

    def stop(self, container_name: str) -> None:
        cmd = ["docker", "rm", "-f", container_name]
        self._last_command = cmd
        subprocess.run(cmd, check=True)

    def list_bc_containers(self) -> list[ContainerInfo]:
        # list all containers (running + stopped) whose name starts with bc-
        result = subprocess.run(
            ["docker", "ps", "-a", "--filter", "name=^bc-",
             "--format", "{{.Names}}\t{{.Status}}"],
            capture_output=True, text=True, check=False,
        )
        infos: list[ContainerInfo] = []
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            parts = line.split("\t", 1)
            name = parts[0].strip()
            status = parts[1].strip() if len(parts) > 1 else ""
            running = status.lower().startswith("up")
            infos.append(ContainerInfo(name=name, running=running))
        return infos

    def get_mounts(self, container_name: str) -> list[ContainerMount]:
        result = subprocess.run(
            ["docker", "inspect", "--format",
             "{{range .Mounts}}{{.Type}}\t{{.Source}}\t{{.Destination}}\n{{end}}",
             container_name],
            capture_output=True, text=True, check=False,
        )
        mounts: list[ContainerMount] = []
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                mounts.append(ContainerMount(
                    type=parts[0].strip(),
                    source=parts[1].strip(),
                    destination=parts[2].strip(),
                ))
        return mounts

    def last_command(self) -> list[str]:
        return self._last_command

    def network_exists(self, network_name: str) -> bool:
        result = subprocess.run(
            ["docker", "network", "ls", "--filter", f"name=^{network_name}$",
             "--format", "{{.Name}}"],
            capture_output=True, text=True, check=False,
        )
        return network_name in result.stdout.split()

    def network_create(self, network_name: str) -> None:
        cmd = ["docker", "network", "create", network_name]
        self._last_command = cmd
        subprocess.run(cmd, check=True)

    def messaging_db_reachable(self, dsn: str) -> bool:
        """Probe the messaging database at ``dsn`` for reachability.

        Uses ``shop-msg ping`` if available, falling back to a TCP connect
        against the host:port parsed from the DSN.  Any failure (unparseable
        DSN, refused connection, timeout) returns False.
        """
        if not dsn:
            return False
        # Prefer the messaging BC's own readiness probe when present.
        probe = subprocess.run(
            ["shop-msg", "ping", "--dsn", dsn],
            capture_output=True, text=True, check=False,
        )
        if probe.returncode == 0:
            return True
        # Fall back to a raw TCP connect against the DSN's host:port.
        import socket
        from urllib.parse import urlparse
        try:
            parsed = urlparse(dsn)
            host = parsed.hostname
            port = parsed.port or 5432
            if not host:
                return False
            with socket.create_connection((host, port), timeout=2.0):
                return True
        except (OSError, ValueError):
            return False

    def health_status(self, container_name: str) -> str:
        """Read the container's Docker health status via docker inspect."""
        result = subprocess.run(
            ["docker", "inspect", "--format",
             "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}",
             container_name],
            capture_output=True, text=True, check=False,
        )
        status = result.stdout.strip()
        return status or "none"

    def agent_vault_reachable(self, broker_address: str) -> bool:
        """Probe the agent-vault broker at ``broker_address`` for reachability.

        Parses a host:port (or URL) out of ``broker_address`` and attempts a
        TCP connect.  Any failure (empty/unparseable address, refused
        connection, timeout) returns False so launch fails fast with a
        readiness error rather than engaging an agent that can authenticate
        to nothing.
        """
        if not broker_address:
            return False
        import socket
        from urllib.parse import urlparse
        try:
            addr = broker_address
            if "://" not in addr:
                addr = "tcp://" + addr
            parsed = urlparse(addr)
            host = parsed.hostname
            port = parsed.port
            if not host or not port:
                return False
            with socket.create_connection((host, port), timeout=2.0):
                return True
        except (OSError, ValueError):
            return False

    def wait_for_pane_marker(
        self,
        container_name: str,
        tmux_session: str,
        marker: str,
        timeout_seconds: float,
        poll_interval_seconds: float = 0.5,
    ) -> bool:
        """Poll tmux capture-pane until marker is observed or timeout elapses."""
        deadline = time.monotonic() + timeout_seconds
        # The agent tmux session is owned by vscode (see
        # controller.AGENT_CONTAINER_USER); capture-pane against a
        # vscode-owned tmux server must run as vscode or tmux refuses the
        # cross-user connection.
        capture_cmd = [
            "docker", "exec", "-u", "vscode", container_name,
            "tmux", "capture-pane", "-p", "-t", tmux_session,
        ]
        while True:
            result = subprocess.run(
                capture_cmd, capture_output=True, text=True, check=False,
            )
            if marker in result.stdout:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(poll_interval_seconds)


# ---------------------------------------------------------------------------
# Real registry driver (shells out to docker buildx imagetools)
# ---------------------------------------------------------------------------

class RealRegistryDriver:
    """Production RegistryDriver that resolves a tag's current registry digest.

    Uses ``docker buildx imagetools inspect --format '{{.Manifest.Digest}}'``
    to read the registry-current digest for the given image reference.  Falls
    back to the original reference if resolution fails (so launch is not made
    strictly dependent on registry reachability at run time).
    """

    def resolve_digest(self, image_ref: str) -> str:
        result = subprocess.run(
            ["docker", "buildx", "imagetools", "inspect",
             "--format", "{{.Manifest.Digest}}", image_ref],
            capture_output=True, text=True, check=False,
        )
        digest = result.stdout.strip()
        return digest or image_ref
