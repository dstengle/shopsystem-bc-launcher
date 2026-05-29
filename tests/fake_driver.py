"""
FakeDockerDriver — in-memory test double for DockerDriver.

Records calls and returns pre-configured state.  Tests set up state before
running the controller under test, then assert on the recorded calls.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field

from bc_launcher.driver import ContainerInfo, ContainerMount


@dataclass
class ExecCall:
    """Records one exec_run or exec_interactive call."""
    container: str
    command: list[str]


class FakeDockerDriver:
    """
    Fully in-memory DockerDriver for tests.

    State is pre-configured by setting attributes before the test action runs.
    """

    def __init__(self) -> None:
        # Set of currently 'running' containers by name
        self._running: set[str] = set()

        # Canned tmux session map: container_name -> set of session names
        self._tmux_sessions: dict[str, set[str]] = {}

        # Canned tmux pane content: container_name -> pane text
        self._tmux_pane: dict[str, str] = {}

        # Recorded exec calls
        self.exec_calls: list[ExecCall] = []

        # Recorded interactive exec calls
        self.interactive_calls: list[ExecCall] = []

        # Last run command (updated by every operation)
        self._last_command: list[str] = []

        # Last docker run command specifically (only updated by run(), not exec_run())
        self._last_run_command: list[str] = []

        # Canned mounts per container
        self._mounts: dict[str, list[ContainerMount]] = {}

        # All known containers (includes stopped), used by list_bc_containers
        self._all_containers: dict[str, bool] = {}  # name -> running

        # Docker networks: name -> exists bool
        self._networks: set[str] = set()

        # Ordered log of top-level operations for before/after assertions
        # Each entry is a tuple: ("network_create", network_name) or ("run", container_name)
        self.operation_log: list[tuple[str, str]] = []

        # Recorded network create calls
        self.network_create_calls: list[str] = []

        # Per-container run commands indexed by container name (for multi-launch scenarios)
        self._run_commands_by_container: dict[str, list[str]] = {}

        # Pane-marker simulation: list of (container_name, session, marker)
        # tuples that wait_for_pane_marker should treat as "never observed"
        # (i.e. simulate the timeout path).  Anything not listed is treated
        # as observed on the first poll (success path).
        self._marker_timeouts: set[tuple[str, str, str]] = set()

        # Record of wait_for_pane_marker invocations so tests can assert
        # exactly which markers the controller polled for and in what order.
        self.wait_for_marker_calls: list[tuple[str, str, str]] = []

    # --- Setup helpers (called by step definitions) ---

    def set_network(self, network_name: str, exists: bool = True) -> None:
        if exists:
            self._networks.add(network_name)
        else:
            self._networks.discard(network_name)

    def set_running(self, container_name: str, running: bool = True) -> None:
        if running:
            self._running.add(container_name)
            self._all_containers[container_name] = True
        else:
            self._running.discard(container_name)
            self._all_containers[container_name] = False

    def add_tmux_session(self, container_name: str, session_name: str) -> None:
        self._tmux_sessions.setdefault(container_name, set()).add(session_name)

    def set_tmux_pane_content(self, container_name: str, content: str) -> None:
        self._tmux_pane[container_name] = content

    def set_mounts(self, container_name: str, mounts: list[ContainerMount]) -> None:
        self._mounts[container_name] = mounts

    # --- DockerDriver protocol implementation ---

    def is_running(self, container_name: str) -> bool:
        return container_name in self._running

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
        self._last_command = cmd
        self._last_run_command = cmd
        self._run_commands_by_container[container_name] = list(cmd)
        self.operation_log.append(("run", container_name))
        # Mark as running and record configured mounts
        self._running.add(container_name)
        self._all_containers[container_name] = True
        # Convert mount tuples to ContainerMount objects and store
        mount_objs = [
            ContainerMount(type=t, source=s, destination=d)
            for t, s, d, _ro in mounts
        ]
        self._mounts[container_name] = mount_objs

    def simulate_marker_timeout(
        self, container_name: str, tmux_session: str, marker: str
    ) -> None:
        """Configure a (container, session, marker) tuple to time out.

        Used by tests exercising the readiness-poll-timeout warning path:
        the controller calls wait_for_pane_marker; the fake returns False
        for any tuple registered here; the controller should then emit
        a stderr warning identifying the step that failed.
        """
        self._marker_timeouts.add((container_name, tmux_session, marker))

    def wait_for_pane_marker(
        self,
        container_name: str,
        tmux_session: str,
        marker: str,
        timeout_seconds: float,
        poll_interval_seconds: float = 0.5,
    ) -> bool:
        """Deterministic marker simulation: success unless registered to time out."""
        self.wait_for_marker_calls.append((container_name, tmux_session, marker))
        if (container_name, tmux_session, marker) in self._marker_timeouts:
            return False
        return True

    def network_exists(self, network_name: str) -> bool:
        return network_name in self._networks

    def network_create(self, network_name: str) -> None:
        self._networks.add(network_name)
        self.network_create_calls.append(network_name)
        self.operation_log.append(("network_create", network_name))

    def exec_run(
        self, container_name: str, command: list[str]
    ) -> subprocess.CompletedProcess:
        self.exec_calls.append(ExecCall(container=container_name, command=command))
        self._last_command = ["docker", "exec", container_name] + command

        # Simulate tmux has-session
        if command[:3] == ["tmux", "has-session", "-t"]:
            session = command[3] if len(command) > 3 else ""
            sessions = self._tmux_sessions.get(container_name, set())
            rc = 0 if session in sessions else 1
            return subprocess.CompletedProcess(command, rc, "", "")

        # Simulate tmux new-session
        if command[:3] == ["tmux", "new-session", "-d"]:
            session = command[command.index("-s") + 1] if "-s" in command else "default"
            self._tmux_sessions.setdefault(container_name, set()).add(session)
            return subprocess.CompletedProcess(command, 0, "", "")

        # Simulate tmux capture-pane
        if command[:3] == ["tmux", "capture-pane", "-p"]:
            pane = self._tmux_pane.get(container_name, "")
            return subprocess.CompletedProcess(command, 0, pane, "")

        # Simulate tmux send-keys
        if command[:2] == ["tmux", "send-keys"]:
            return subprocess.CompletedProcess(command, 0, "", "")

        # Simulate git clone
        if command[0] == "git" and command[1] == "clone":
            return subprocess.CompletedProcess(command, 0, "", "")

        # Simulate bd dolt pull
        if command[:3] == ["bd", "dolt", "pull"]:
            return subprocess.CompletedProcess(command, 0, "", "")

        # Default: success
        return subprocess.CompletedProcess(command, 0, "", "")

    def exec_interactive(self, container_name: str, command: list[str]) -> None:
        self.interactive_calls.append(ExecCall(container=container_name, command=command))
        self._last_command = ["docker", "exec", "-it", container_name] + command

    def stop(self, container_name: str) -> None:
        self._last_command = ["docker", "rm", "-f", container_name]
        self._running.discard(container_name)
        self._all_containers[container_name] = False

    def list_bc_containers(self) -> list[ContainerInfo]:
        return [
            ContainerInfo(name=name, running=running)
            for name, running in self._all_containers.items()
        ]

    def get_mounts(self, container_name: str) -> list[ContainerMount]:
        return self._mounts.get(container_name, [])

    def last_command(self) -> list[str]:
        return self._last_command

    def last_run_command(self) -> list[str]:
        """Return the last docker run command (excludes exec_run / exec_interactive calls)."""
        return self._last_run_command

    def run_command_for_container(self, container_name: str) -> list[str]:
        """Return the docker run command recorded for a specific container."""
        return self._run_commands_by_container.get(container_name, [])
