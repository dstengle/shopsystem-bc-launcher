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
    """Records one exec_run or exec_interactive call.

    ``user`` is the container user the command ran as (``docker exec -u
    <user>``), or ``None`` for the default (root in the BC image).  Tests
    assert on this to verify the tmux session and its clients all run as
    vscode end-to-end.
    """
    container: str
    command: list[str]
    user: str | None = None


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

        # --- Interactive-agent submission model (lead-xsmn / lead-hyee /
        #     lead-lez1 / lead-9q0f) ---
        # The bug being pinned (empirically narrowed under lead-9q0f): a SINGLE
        # `tmux send-keys -t agent '<text>' Enter` exec_run concatenates the
        # whole keystream into ONE pty write() syscall.  Claude Code's TUI
        # treats single-write payloads above ~70 bytes as a paste and absorbs
        # the trailing CR into the input buffer rather than submitting it — so
        # a single text+Enter invocation leaves the prompt UNSUBMITTED, idle in
        # the buffer.  Only TWO discrete send-keys invocations — text-only
        # first, then a bare Enter second — produce two discrete pty writes
        # separated by a kernel-scheduling gap, which the TUI processes as a
        # discrete submit keypress and commits.
        #
        # This model makes the FakeDockerDriver a faithful stand-in for the
        # real tmux send-keys call shape:
        #   * send-keys '<text>'                  -> buffer = text (idle)
        #   * send-keys (bare) Enter              -> commit whatever is buffered
        #   * send-keys '<text>' Enter (one call) -> PASTE: buffer = text, idle
        #                                            (the trailing CR is absorbed
        #                                            into the buffer, NOT a submit)
        #   * send-keys '<text>\n' (baked LF)     -> buffer = text (idle)
        # The single-call text+Enter shape and the baked-LF shape are BOTH the
        # regression; only the two-call (text, then bare Enter) shape commits.
        #
        # container_name -> dict with keys:
        #   "buffer":     text currently sitting unsubmitted in the input box
        #   "processing": prompt text the agent has committed and is working on
        self._agent_state: dict[str, dict[str, str | None]] = {}

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
        self,
        container_name: str,
        command: list[str],
        user: str | None = None,
    ) -> subprocess.CompletedProcess:
        self.exec_calls.append(
            ExecCall(container=container_name, command=command, user=user)
        )
        prefix = ["docker", "exec"]
        if user is not None:
            prefix += ["-u", user]
        self._last_command = prefix + [container_name] + command

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

        # Simulate tmux capture-pane.  Surface the agent-working state-marker
        # when the modelled agent has committed input and is processing it;
        # otherwise fall back to whatever pane content was configured.  This
        # is what `bc-container monitor` reads, so it is the host-reachable
        # observability surface for scenario 5ef728039884a9a2.
        if command[:3] == ["tmux", "capture-pane", "-p"]:
            state = self._agent_state.get(container_name)
            if state and state.get("processing"):
                pane = f"Working… (processing {state['processing']!r})"
            else:
                pane = self._tmux_pane.get(container_name, "")
            return subprocess.CompletedProcess(command, 0, pane, "")

        # Simulate tmux send-keys with faithful submit semantics (see
        # _agent_state above).  Strip the "-t <session>" target tokens, then
        # treat the remaining tokens as the send-keys payload.  Input is
        # COMMITTED to the agent's main loop only when a non-empty text token
        # is followed by a DISCRETE "Enter" key-name token.  A text token with
        # an appended "\n" (the buggy shape) populates the buffer but does NOT
        # submit.
        if command[:2] == ["tmux", "send-keys"]:
            payload = command[2:]
            # Drop the "-t <session>" pair if present.
            if payload[:1] == ["-t"]:
                payload = payload[2:]
            state = self._agent_state.setdefault(
                container_name, {"buffer": None, "processing": None}
            )
            if payload and payload[-1] == "Enter":
                text_tokens = payload[:-1]
                text = " ".join(text_tokens)
                if text:
                    # Non-empty text AND Enter in ONE invocation: the paste
                    # regression (lead-9q0f).  The single pty write is absorbed
                    # as a paste — the text lands in the buffer and the trailing
                    # CR is swallowed into it, so NOTHING is submitted.  Agent
                    # stays idle.
                    state["buffer"] = text
                else:
                    # Bare Enter (e.g. trust-accept, the two-call submit's
                    # second invocation, or the empty-text inject workaround):
                    # a discrete submit keypress — commit whatever is buffered.
                    if state.get("buffer"):
                        state["processing"] = state["buffer"]
                        state["buffer"] = None
            else:
                # No discrete trailing Enter.  Any text (including a token with
                # a baked-in "\n") lands in the buffer UNSUBMITTED — the agent
                # stays idle.  This is the regression the scenarios guard.
                text = " ".join(payload)
                if text:
                    state["buffer"] = text
            return subprocess.CompletedProcess(command, 0, "", "")

        # Simulate git clone
        if command[0] == "git" and command[1] == "clone":
            return subprocess.CompletedProcess(command, 0, "", "")

        # Simulate bd dolt pull
        if command[:3] == ["bd", "dolt", "pull"]:
            return subprocess.CompletedProcess(command, 0, "", "")

        # Default: success
        return subprocess.CompletedProcess(command, 0, "", "")

    def exec_interactive(
        self,
        container_name: str,
        command: list[str],
        user: str | None = None,
    ) -> None:
        self.interactive_calls.append(
            ExecCall(container=container_name, command=command, user=user)
        )
        prefix = ["docker", "exec", "-it"]
        if user is not None:
            prefix += ["-u", user]
        self._last_command = prefix + [container_name] + command

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

    # --- Interactive-agent submission model queries (lead-xsmn / lead-hyee) ---

    def agent_committed_prompt(self, container_name: str) -> str | None:
        """Return the prompt the modelled agent has committed and is processing.

        ``None`` means the agent is idle (no input committed) — either nothing
        was sent, or text was sent without a discrete trailing ``Enter`` and is
        therefore sitting unsubmitted in the input buffer.
        """
        state = self._agent_state.get(container_name)
        return state.get("processing") if state else None

    def agent_buffer(self, container_name: str) -> str | None:
        """Return text sitting unsubmitted in the input buffer (or ``None``)."""
        state = self._agent_state.get(container_name)
        return state.get("buffer") if state else None

    def send_keys_calls(self, container_name: str) -> list[ExecCall]:
        """Return all recorded tmux send-keys exec calls for the container."""
        return [
            c for c in self.exec_calls
            if c.container == container_name and c.command[:2] == ["tmux", "send-keys"]
        ]
