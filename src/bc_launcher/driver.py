"""
Docker driver interface and real implementation.

The DockerDriver protocol is the seam between bc_launcher business logic and
the real Docker daemon.  Tests inject a FakeDockerDriver; production code uses
RealDockerDriver which shells out to the docker CLI.
"""
from __future__ import annotations

import subprocess
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
        mounts: list[tuple[str, str, str]],  # (type, source, dest)
        network: str | None,
        detach: bool,
    ) -> None:
        """Start a new container."""
        ...

    def exec_run(self, container_name: str, command: list[str]) -> subprocess.CompletedProcess:
        """Execute a command inside a running container."""
        ...

    def exec_interactive(self, container_name: str, command: list[str]) -> None:
        """Execute an interactive command inside a running container (replaces process)."""
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
        mounts: list[tuple[str, str, str]],
        network: str | None,
        detach: bool,
    ) -> None:
        cmd = ["docker", "run", "--name", container_name]
        if detach:
            cmd.append("-d")
        for key, val in env.items():
            cmd += ["-e", f"{key}={val}"]
        for mount_type, source, dest in mounts:
            cmd += ["--mount", f"type={mount_type},source={source},target={dest}"]
        if network:
            cmd += ["--network", network]
        cmd.append(image)
        self._last_command = cmd
        subprocess.run(cmd, check=True)

    def exec_run(self, container_name: str, command: list[str]) -> subprocess.CompletedProcess:
        cmd = ["docker", "exec", container_name] + command
        self._last_command = cmd
        return subprocess.run(cmd, capture_output=True, text=True, check=False)

    def exec_interactive(self, container_name: str, command: list[str]) -> None:
        import os
        cmd = ["docker", "exec", "-it", container_name] + command
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
