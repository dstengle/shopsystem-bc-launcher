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


class FakeRegistryDriver:
    """In-memory RegistryDriver test double (scenario af2f03d3ac519cb5).

    Simulates the registry resolving a tag (e.g. bc-base "latest") to a
    digest.  A test configures the registry-current digest via
    ``set_registry_digest(image_ref, digest)``; ``resolve_digest`` returns it
    and records the call so the test can assert launch resolved the tag
    before starting the container.

    This fake belongs to scenario 39 ONLY — it is NOT shared with the
    workflow/CI scenarios 37/38/41, which carry no in-src registry seam.
    """

    def __init__(self) -> None:
        # image_ref -> registry-current digest
        self._registry_digests: dict[str, str] = {}
        # Ordered record of resolve_digest(image_ref) calls.
        self.resolve_calls: list[str] = []

    def set_registry_digest(self, image_ref: str, digest: str) -> None:
        """Configure the digest the registry currently exposes for image_ref."""
        self._registry_digests[image_ref] = digest

    def resolve_digest(self, image_ref: str) -> str:
        self.resolve_calls.append(image_ref)
        # Return the configured registry-current digest; if none configured,
        # echo the reference back unchanged (no resolution).
        return self._registry_digests.get(image_ref, image_ref)


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

        # --- Messaging readiness / beads / health simulation ---
        # Messaging reachability is modelled as reachable-by-default so that
        # existing launch scenarios (which configure a host SHOPMSG_DSN but
        # never set up a live database) keep injecting their startup prompt.
        # The readiness scenarios that pin the unreachable path register the
        # offending DSN here explicitly via set_dsn_reachable(dsn, False).
        self._unreachable_dsns: set[str] = set()

        # Containers that have passed their readiness sequence (idempotent
        # re-run support).
        self._ready_containers: set[str] = set()

        # Per-container beads issue_prefix configured inside .beads.  Empty /
        # missing means beads is NOT functionally usable: `bd create` fails.
        self._beads_prefix: dict[str, str] = {}

        # Monotonic counter for synthesising beads issue ids.
        self._beads_seq: dict[str, int] = {}

        # Containers whose beads is forced unusable regardless of prefix
        # (models the "bd create exits non-zero" health scenario).
        self._beads_broken: set[str] = set()

        # Explicit health-status overrides per container (when a test wants
        # to assert a docker-inspect status directly rather than derive it).
        self._health_override: dict[str, str] = {}

        # The DSN configured for each container (recorded from docker run -e).
        self._container_dsn: dict[str, str] = {}

        # --- shop-templates pour model (lead-dlrx, scenario 75ae95be0ecf1640) ---
        # The workspace's ".claude/skills/" directory, modelled per container as
        # the set of skill-group entries present.  Empty / missing means the
        # pour has NOT populated it.  A `shop-templates pour` exec_run run inside
        # the workspace directory populates it with the shop-templates
        # skill-group, giving the scenario's "skills populated after launch"
        # assertion teeth: skip the pour and the set stays empty (scenario
        # FAILS).
        self._workspace_skills: dict[str, set[str]] = {}
        # Ordered record of (container, command) for shop-templates pour calls,
        # so tests can assert the pour ran inside the workspace directory.
        self.pour_calls: list[ExecCall] = []
        # The skill-group entries a pour deposits into ".claude/skills/".
        self.SHOP_TEMPLATES_SKILL_GROUP = frozenset(
            {"shop-templates"}
        )

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

    def set_dsn_reachable(self, dsn: str, reachable: bool = True) -> None:
        """Mark a DSN reachable (default) or unreachable for readiness checks.

        Reachability is modelled as reachable-by-default; only DSNs
        explicitly marked unreachable here fail the readiness barrier.
        """
        if reachable:
            self._unreachable_dsns.discard(dsn)
        else:
            self._unreachable_dsns.add(dsn)

    def set_container_dsn(self, container_name: str, dsn: str) -> None:
        """Record the DSN configured for a (possibly pre-existing) container."""
        self._container_dsn[container_name] = dsn

    def mark_ready(self, container_name: str) -> None:
        """Mark a container as having passed its readiness sequence."""
        self._ready_containers.add(container_name)

    def is_marked_ready(self, container_name: str) -> bool:
        return container_name in self._ready_containers

    def set_beads_prefix(self, container_name: str, prefix: str) -> None:
        """Pre-configure a beads issue_prefix inside a container's .beads."""
        self._beads_prefix[container_name] = prefix

    def beads_prefix(self, container_name: str) -> str:
        """Return the issue_prefix configured inside the container's .beads."""
        return self._beads_prefix.get(container_name, "")

    def set_beads_broken(self, container_name: str, broken: bool = True) -> None:
        """Force `bd create` to fail inside the container regardless of prefix."""
        if broken:
            self._beads_broken.add(container_name)
        else:
            self._beads_broken.discard(container_name)

    def set_health_override(self, container_name: str, status: str) -> None:
        self._health_override[container_name] = status

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
            if key == "SHOPMSG_DSN":
                self._container_dsn[container_name] = val
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

    def messaging_db_reachable(self, dsn: str) -> bool:
        """Reachable-by-default unless the DSN was marked unreachable."""
        return bool(dsn) and dsn not in self._unreachable_dsns

    def health_status(self, container_name: str) -> str:
        """Compose the container's health status.

        An explicit override (set_health_override) wins.  Otherwise the
        container is "healthy" only when beads is functionally usable inside
        it (a non-empty issue_prefix configured and not forced broken) AND
        the messaging database at its configured DSN is reachable; any other
        state is "unhealthy".  Returns "none" if the container is unknown.
        """
        if container_name in self._health_override:
            return self._health_override[container_name]
        if container_name not in self._all_containers:
            return "none"
        prefix = self._beads_prefix.get(container_name, "")
        beads_ok = bool(prefix) and container_name not in self._beads_broken
        dsn = self._container_dsn.get(container_name, "")
        db_ok = bool(dsn) and dsn not in self._unreachable_dsns
        return "healthy" if (beads_ok and db_ok) else "unhealthy"

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

        # Simulate `bd config set issue_prefix <prefix>` — the launcher's
        # beads-usability fix.  Records the configured prefix so subsequent
        # `bd create` / `bd ready` behave as functionally usable.
        if command[:3] == ["bd", "config", "set"] and len(command) >= 5 \
                and command[3] == "issue_prefix":
            self._beads_prefix[container_name] = command[4]
            return subprocess.CompletedProcess(command, 0, "", "")

        # Simulate `bd create ...` — exits zero and emits a new issue id
        # carrying the configured prefix ONLY when beads is functionally
        # usable (a non-empty issue_prefix is configured and beads is not
        # forced broken).  Otherwise it exits non-zero, mirroring the
        # "database not initialized: issue_prefix config is missing" failure.
        if command[:2] == ["bd", "create"]:
            prefix = self._beads_prefix.get(container_name, "")
            if not prefix or container_name in self._beads_broken:
                return subprocess.CompletedProcess(
                    command, 1, "",
                    "database not initialized: issue_prefix config is missing\n",
                )
            seq = self._beads_seq.get(container_name, 0) + 1
            self._beads_seq[container_name] = seq
            issue_id = f"{prefix}-{seq}"
            return subprocess.CompletedProcess(command, 0, f"{issue_id}\n", "")

        # Simulate `bd ready` — exits zero when beads is functionally usable.
        if command[:2] == ["bd", "ready"]:
            prefix = self._beads_prefix.get(container_name, "")
            if not prefix or container_name in self._beads_broken:
                return subprocess.CompletedProcess(
                    command, 1, "",
                    "database not initialized: issue_prefix config is missing\n",
                )
            return subprocess.CompletedProcess(command, 0, "", "")

        # Simulate `shop-templates pour ...` — the launch step that populates
        # the workspace's ".claude/skills/" with the shop-templates skill-group
        # (lead-dlrx, scenario 75ae95be0ecf1640).  The pour is recognised when
        # the command runs the shop-templates "pour" subcommand AND names the
        # container workspace directory (so the assertion that it ran INSIDE the
        # workspace directory has teeth).  Modelling the populate effect here is
        # what gives the scenario's "skills populated after launch" Then step
        # its teeth: skip the pour and _workspace_skills stays empty → FAIL.
        if command[:2] == ["shop-templates", "pour"]:
            self.pour_calls.append(
                ExecCall(container=container_name, command=command, user=user)
            )
            # Only a pour that targets the workspace directory populates it.
            if "/workspace" in command:
                self._workspace_skills.setdefault(container_name, set()).update(
                    self.SHOP_TEMPLATES_SKILL_GROUP
                )
            return subprocess.CompletedProcess(command, 0, "", "")

        # Default: success
        return subprocess.CompletedProcess(command, 0, "", "")

    def workspace_skills(self, container_name: str) -> set[str]:
        """Return the skill-group entries present in the workspace .claude/skills/."""
        return set(self._workspace_skills.get(container_name, set()))

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
