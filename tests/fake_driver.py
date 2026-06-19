"""
FakeDockerDriver — in-memory test double for DockerDriver.

Records calls and returns pre-configured state.  Tests set up state before
running the controller under test, then assert on the recorded calls.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field

from bc_launcher.driver import ContainerInfo, ContainerMount

# Mirror the launcher's container-side constants so the fake can model
# `.beads` ownership transfer (lead-kjv7 DEFECT 3) without importing
# controller internals.
CONTAINER_WORKSPACE = "/workspace"
AGENT_CONTAINER_USER = "vscode"


def is_bd_bootstrap_command(command: list[str]) -> bool:
    """Return True if ``command`` is the launcher's ``bd bootstrap`` provisioning step.

    lead-8268 — the proven-clean recipe (commit 2b7ba61, contract
    @scenario_hash:2904f3a905567b48) runs bootstrap through a login shell so it
    executes in the workspace directory:

        ["bash", "-lc", "cd /workspace && bd bootstrap"]

    `docker exec` carries no implicit cwd, so the ``cd`` wrapper is
    load-bearing.  An earlier launcher form issued the bare vector
    ``["bd", "bootstrap"]``.  This matcher recognises BOTH so the bootstrap
    detection tracks the invocation form rather than pinning a stale one.
    """
    if command[:2] == ["bd", "bootstrap"]:
        return True
    if (
        len(command) >= 3
        and command[0] == "bash"
        and command[1] in ("-lc", "-c")
        and "bd bootstrap" in command[2]
    ):
        return True
    return False


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
    # Per-exec environment injected via ``docker exec -e KEY=VALUE`` (or None
    # when the exec carries no extra env).  bclaunch-5fji uses this to pin the
    # launch-time clone's brokered HTTPS_PROXY + GIT_SSL_CAINFO trust env.
    env: dict[str, str] | None = None


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

        # --- Committed beads registry model (lead-rply) ---
        # The committed prefix the CLONED repo's registry carries at HEAD.
        # This is intentionally DISTINCT from the name-derived prefix
        # (beads_prefix_for): a freshly cloned BC's committed registry may use
        # a prefix the BC name does not imply (e.g. shopsystem-bc-launcher
        # name-derives 'bclauncher' but its committed registry uses
        # 'bclaunch').  Keyed per container; set by the clone simulation.
        self._committed_beads_prefix: dict[str, str] = {}
        # Whether the committed registry has been MATERIALIZED into the working
        # tree (via `git checkout HEAD -- .beads/issues.jsonl`).  On clone the
        # registry is git-tracked at HEAD but ABSENT from the working tree, so
        # this starts False.  `bd config set issue_prefix` only imports the
        # committed registry into the Dolt working set once it is materialized.
        self._beads_registry_materialized: set[str] = set()
        # Containers whose Dolt working set has been provisioned (committed
        # registry imported).  Empty working set => `bd ready` lists nothing
        # and `bd create` cannot adopt the committed issues.
        #
        # lead-ezzr — TEST-FIDELITY.  SUPERSEDES the lead-kjv7 model of
        # `bd dolt pull` → `bd config set issue_prefix` → `bd import`, which
        # passed green on a fix that was EMPIRICALLY broken.  The corrected
        # model pins the REAL `bd bootstrap` mechanism:
        #   * `bd bootstrap` imports the git-tracked JSONL, creates the Dolt
        #     working set (`embeddeddolt/`), derives the prefix from the
        #     imported registry, and yields WRITE-READY — but ONLY when no
        #     bd-created Dolt DB already exists.
        #   * `bd dolt pull` FIRST pre-creates an EMPTY bd-created Dolt DB,
        #     which makes a subsequent `bd bootstrap` a NO-OP ("database
        #     already exists, nothing to do") that leaves the BC WEDGED:
        #     prefix unset, working set unprovisioned.  This is the
        #     self-inflicted lead-vlsu deadlock.
        #   * a separate `bd import` ALSO pre-creates the Dolt DB without
        #     deriving a usable prefix, so it does NOT yield write-ready and
        #     wedges a later bootstrap the same way.
        # So a launcher that reverts to the pull+config+import mechanism now
        # reads RED here (revert-teeth), modelling the real wedged state.
        self._beads_working_set_provisioned: set[str] = set()

        # lead-ezzr — whether a bd-created Dolt DB already exists for the
        # container.  Set by `bd dolt pull` (empty DB) or by a `bd import`
        # that pre-creates the DB.  Its PRESENCE makes a later `bd bootstrap`
        # a no-op ("already exists, nothing to do"), the self-inflicted
        # deadlock.  Bootstrap on a container WITHOUT a pre-existing bd-created
        # DB is the only path that yields write-ready.
        self._beads_db_precreated: set[str] = set()

        # lead-kjv7 DEFECT 4 — whether the embedded-Dolt working-set directory
        # (`/workspace/.beads/embeddeddolt/`) exists.  It is ABSENT after clone
        # and after `bd config set issue_prefix`; it materializes ONLY when the
        # committed registry is imported into the Dolt working set (`bd import`
        # on the materialized jsonl).  This is the directory whose ABSENCE the
        # empirical failure observed.
        self._beads_embeddeddolt_present: set[str] = set()

        # lead-kjv7 DEFECT 3 / DEFECT 4 — ownership of `/workspace/.beads`.
        # Provisioning steps (clone, bd dolt pull, git checkout, bd config,
        # bd import) run as ROOT by default, so the `.beads` tree they create
        # lands root-owned.  The vscode agent then cannot use the backend.
        # Ownership is recorded per container; default "root".  It becomes
        # "vscode" ONLY when a recursive chown to vscode COVERS `.beads`
        # (a chown of /workspace with -R, or a chown that names .beads), OR
        # when the beads provisioning ran as vscode in the first place.
        self._beads_owner: dict[str, str] = {}

        # lead-mf15 — durable ownership of EVERY agent-touched workspace path
        # (scenario @scenario_hash:d9e4ce60e03df361).  The `.beads`-only model
        # above pins the bd backend; this richer model tracks the ownership of
        # each agent-touched path under /workspace (/workspace itself, .git,
        # .beads) so the durable invariant can be asserted: after container
        # init completes (at the moment the agent's tmux session is started),
        # NO agent-touched path may be root-owned.
        #
        # Model:
        #   * Each path starts "root" (the BC image default USER is root and
        #     the clone runs as root).
        #   * A root-context exec (user is None) that WRITES under a path
        #     re-roots that path — modelling the lead-mf15 mid-run re-root
        #     (e.g. a later root-context git op leaving .git/objects/NN/
        #     root-owned).
        #   * A vscode-context exec that writes under a path leaves that path
        #     vscode-owned.
        #   * A recursive `chown -R vscode:vscode /workspace` re-owns ALL
        #     agent-touched paths to vscode.
        # Default per path is "root".
        self._workspace_path_owner: dict[str, dict[str, str]] = {}
        # Snapshot of `_workspace_path_owner` captured at the instant the
        # agent's `tmux new-session` is issued — i.e. the ownership state the
        # vscode agent inherits once container init completes.  None until the
        # agent session is started.
        self._workspace_path_owner_at_agent_start: dict[str, dict[str, str]] = {}

        # Explicit health-status overrides per container (when a test wants
        # to assert a docker-inspect status directly rather than derive it).
        self._health_override: dict[str, str] = {}

        # The DSN configured for each container (recorded from docker run -e).
        self._container_dsn: dict[str, str] = {}

        # --- Agent-vault broker model (ADR-026, lead-hxb8 / lead-v4ih) ---
        # Broker reachability is modelled as reachable-by-default so existing
        # launch scenarios keep engaging their agent.  Readiness scenarios
        # that pin the unreachable path register the offending broker address
        # here via set_agent_vault_reachable(addr, False).
        self._unreachable_brokers: set[str] = set()
        # --- lead-cs7k probe-execution-context model ---
        # Targets reachable from INSIDE a given docker network (docker exec),
        # keyed by network name -> set of host tokens.
        self._network_reachable_targets: dict[str, set[str]] = {}
        # Targets the launcher HOST process cannot resolve (host-context probe
        # fails for these; an inside-network probe still reaches them).
        self._host_unresolvable_targets: set[str] = set()
        # The docker network each container is attached to.
        self._container_network: dict[str, str] = {}
        # Ordered record of (probe_kind, container) for each probe invocation,
        # so tests can assert each probe ran inside the container's network
        # context rather than from the launcher host process.
        self._probe_exec_contexts: list[tuple[str, str | None]] = []
        # The agent-vault broker address configured for each container, so
        # health can fold broker reachability into its status.
        self._container_broker: dict[str, str] = {}
        # The HTTPS_PROXY env value recorded from docker run -e for a
        # container, so security scenarios can assert the proxy points at the
        # broker rather than the container holding a real credential.
        self._container_proxy_env: dict[str, str] = {}
        # The FULL env dict recorded from docker run -e for a container, so
        # agent-vault scenarios (bclaunch-5hi / bclaunch-7pf) can assert the
        # AGENT_VAULT_* and TLS-trust env vars the launcher injects.
        self._container_env: dict[str, dict[str, str]] = {}
        # The FULL mount list (type, source, dest, readonly) recorded from
        # docker run --mount for a container, so the CA-mount scenario
        # (bclaunch-7pf) can assert the broker CA is mounted read-only at the
        # fixed container path.  ContainerMount drops the readonly flag, so
        # this preserves it.
        self._container_mounts_full: dict[
            str, list[tuple[str, str, str, bool]]
        ] = {}

        # --- shop-templates skill-refresh model (lead-dlrx scenario ---------
        # 75ae95be0ecf1640; lead-q5k7 bugfix DEFECT-fidelity) ----------------
        # The workspace's ".claude/skills/" directory, modelled per container
        # as the set of skill-group entries present.  Empty / missing means
        # the refresh has NOT populated it.
        #
        # lead-q5k7 fidelity: the refresh is recognised ONLY when the
        # controller execs the REAL, VALID invocation
        # `shop-templates update --target <ws> --shop-type <bc|lead>`.  The
        # bc-base `shop-templates` CLI has NO `pour` subcommand (valid:
        # list/show/bootstrap/update) and the flag is `--target`, NOT
        # `--workspace` — so a `pour`/`--workspace` exec is modelled as the
        # REAL FAILURE it is (non-zero, argparse-style stderr) and deposits
        # NOTHING.  This is what gives criteria A/B their teeth: a launcher
        # that execs the invalid command can no longer read green, and the
        # false-success "Poured ..." log it used to append on that failure is
        # now caught because the controller checks the result and fails.
        self._workspace_skills: dict[str, set[str]] = {}
        # The bc-base image's shop-type marker per container ("bc"/"lead"),
        # read by the controller from `.claude/shop/type.md`.  Defaults to
        # "bc"; tests may override via set_shop_type().
        self._shop_type: dict[str, str] = {}
        # Ordered record of shop-templates skill-refresh exec calls, so tests
        # can assert the refresh ran the VALID command inside the workspace.
        self.refresh_calls: list[ExecCall] = []
        # The skill-group entries a successful refresh deposits into
        # ".claude/skills/".  "bc-router-health" models criterion C: the
        # refreshed bc-router skill carries the lead-80t0 health step (the
        # 143-line / health-bearing copy), overwriting any stale committed
        # copy.  A refresh that does not run leaves this absent.
        self.SHOP_TEMPLATES_SKILL_GROUP = frozenset(
            {"shop-templates", "bc-router-health"}
        )
        # Back-compat alias: prior tests referenced `pour_calls`.
        self.pour_calls = self.refresh_calls
        # lead-q5k7 criterion B — containers for which even the VALID
        # `shop-templates update` exec fails (e.g. the package errors at
        # runtime).  Models the REAL failure surface so a controller that
        # logs false success on a failed refresh cannot read green: a failed
        # refresh deposits NO skills and returns non-zero.
        self._skill_refresh_fails: set[str] = set()

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

    def set_agent_vault_reachable(
        self, broker_address: str, reachable: bool = True
    ) -> None:
        """Mark an agent-vault broker reachable (default) or unreachable.

        Reachability is modelled as reachable-by-default; only brokers
        explicitly marked unreachable here fail the readiness barrier.
        """
        if reachable:
            self._unreachable_brokers.discard(broker_address)
        else:
            self._unreachable_brokers.add(broker_address)

    # --- lead-cs7k: probe-execution-context model -------------------------
    # The readiness probes must run from INSIDE the launched container's
    # network context, NOT from the launcher host process.  For a second
    # product the launcher host is not attached to the product's docker
    # network, so a host-process socket connect to "dummyco-postgres" or the
    # product broker host false-fails — while a `docker exec` into the
    # container (which IS on the product network) reaches both fine.
    #
    # Model: reachability is keyed by EXECUTION CONTEXT.  A probe invoked
    # WITHOUT a container runs from the launcher host (the legacy
    # host-reachability sets above).  A probe invoked WITH a container runs
    # inside that container's network; reachability is resolved against the
    # per-network reachable-target set registered here.  A target absent from
    # the network set but absent from the host-unreachable set is still
    # reachable-by-default (so existing scenarios are unaffected); the
    # second-product bug is modelled by registering a target as reachable
    # ONLY from inside the network while the launcher host cannot resolve it.
    def set_network_target_reachable_from_inside(
        self, network: str, target: str
    ) -> None:
        """Mark ``target`` reachable from INSIDE ``network`` (docker exec),
        even when the launcher host cannot resolve it."""
        self._network_reachable_targets.setdefault(network, set()).add(target)

    def set_host_cannot_resolve(self, target: str) -> None:
        """Mark ``target`` unresolvable from the launcher HOST process.

        A probe that runs from the host against this target fails; only a
        probe executed from inside the container's network reaches it.
        """
        self._host_unresolvable_targets.add(target)

    def set_container_network(self, container_name: str, network: str) -> None:
        """Record the docker network the container is attached to."""
        self._container_network[container_name] = network

    def probe_exec_contexts(self) -> list[tuple[str, str | None]]:
        """Return the recorded (probe_kind, container) execution contexts.

        ``container`` is None when the probe ran from the launcher host
        process and the container name when it ran via docker exec inside the
        container's network.
        """
        return list(self._probe_exec_contexts)

    @staticmethod
    def _target_host(addr: str) -> str:
        """Extract the bare host[:port] target token from a DSN/broker addr."""
        from urllib.parse import urlparse
        s = addr.strip()
        parsed = urlparse(s if "://" in s else "tcp://" + s)
        host = parsed.hostname or ""
        return host

    def set_container_broker(self, container_name: str, broker_address: str) -> None:
        """Record the agent-vault broker configured for a container."""
        self._container_broker[container_name] = broker_address

    def container_proxy_env(self, container_name: str) -> str:
        """Return the HTTPS_PROXY value recorded from the container's docker run."""
        return self._container_proxy_env.get(container_name, "")

    def container_env(self, container_name: str) -> dict[str, str]:
        """Return the full env dict recorded from the container's docker run."""
        return dict(self._container_env.get(container_name, {}))

    def container_mounts_full(
        self, container_name: str
    ) -> list[tuple[str, str, str, bool]]:
        """Return the full mount tuples (incl. readonly) for a container's run."""
        return list(self._container_mounts_full.get(container_name, []))

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

    def set_committed_beads_prefix(
        self, container_name: str, prefix: str
    ) -> None:
        """Model the committed prefix the cloned repo's registry carries.

        This is the prefix `git show HEAD:.beads/issues.jsonl` reveals after
        clone — intentionally DISTINCT from the name-derived prefix so the
        launcher's "adopt the committed prefix" behaviour (lead-rply) has teeth.
        """
        self._committed_beads_prefix[container_name] = prefix

    def set_shop_type(self, container_name: str, shop_type: str) -> None:
        """Model the cloned shop's `.claude/shop/type.md` marker (lead-q5k7).

        This is the value the controller reads to derive the
        `shop-templates update --shop-type <bc|lead>` argument.  Defaults to
        "bc" when unset.
        """
        self._shop_type[container_name] = shop_type

    def set_skill_refresh_fails(
        self, container_name: str, fails: bool = True
    ) -> None:
        """Model a `shop-templates update` that FAILS at runtime (lead-q5k7
        criterion B), so the controller's result-check + error-surfacing can
        be pinned: a failed refresh must NOT log success.
        """
        if fails:
            self._skill_refresh_fails.add(container_name)
        else:
            self._skill_refresh_fails.discard(container_name)

    def committed_beads_prefix(self, container_name: str) -> str:
        """Return the committed prefix the cloned repo's registry carries."""
        return self._committed_beads_prefix.get(container_name, "")

    def beads_registry_materialized(self, container_name: str) -> bool:
        """True once the committed registry was checked out into the worktree."""
        return container_name in self._beads_registry_materialized

    def beads_working_set_provisioned(self, container_name: str) -> bool:
        """True once the committed registry was imported into the Dolt DB."""
        return container_name in self._beads_working_set_provisioned

    def beads_embeddeddolt_present(self, container_name: str) -> bool:
        """Whether `/workspace/.beads/embeddeddolt/` exists (lead-kjv7 DEFECT 4).

        Absent after clone and after a bare `bd config set issue_prefix`;
        present ONLY once the committed registry has been imported into the
        Dolt working set.  Its absence is the empirical failure surface
        (`bd ready` / `bd create` → "no beads database found").
        """
        return container_name in self._beads_embeddeddolt_present

    def beads_owner(self, container_name: str) -> str:
        """Return the owner of `/workspace/.beads` (lead-kjv7 DEFECT 3).

        Defaults to "root": provisioning runs as root, so the `.beads` tree
        lands root-owned until a recursive chown covers it (or provisioning
        ran as vscode).  The vscode agent cannot use a root-owned backend.
        """
        return self._beads_owner.get(container_name, "root")

    # --- lead-mf15: durable per-path ownership model -----------------------

    # The agent-touched workspace paths whose ownership the durable invariant
    # (scenario @scenario_hash:d9e4ce60e03df361) tracks.
    AGENT_TOUCHED_PATHS = (
        CONTAINER_WORKSPACE,
        f"{CONTAINER_WORKSPACE}/.git",
        f"{CONTAINER_WORKSPACE}/.beads",
    )

    @staticmethod
    def _paths_written_under(command: list[str]) -> set[str]:
        """Return the agent-touched workspace paths a command writes under.

        A command that materially operates on the workspace (clone into it,
        a `git -C /workspace ...` op, `bd bootstrap`, a `shop-templates
        update --target /workspace`, etc.) is modelled as WRITING under the
        relevant agent-touched paths.  A pure chown is NOT a content write
        (it is handled separately as an ownership transfer).
        """
        if not command:
            return set()
        if command[0] == "chown":
            return set()
        written: set[str] = set()
        # git clone <url> /workspace → writes the whole tree, incl. .git
        if command[0] == "git" and "clone" in command:
            written |= {
                CONTAINER_WORKSPACE,
                f"{CONTAINER_WORKSPACE}/.git",
            }
        # any `git -C /workspace ...` op touches /workspace and .git
        if command[0] == "git" and "-C" in command:
            written |= {
                CONTAINER_WORKSPACE,
                f"{CONTAINER_WORKSPACE}/.git",
            }
        # bd bootstrap / bd import / bd dolt pull provision .beads
        if is_bd_bootstrap_command(command) or command[:2] == ["bd", "import"] \
                or command[:3] == ["bd", "dolt", "pull"]:
            written |= {
                CONTAINER_WORKSPACE,
                f"{CONTAINER_WORKSPACE}/.beads",
            }
        # shop-templates update --target /workspace overwrites .claude/ under
        # the workspace.  lead-mf15 (observed 2026-06-18): a path created or
        # re-rooted by this LAST provisioning op — or by a root-context op the
        # container fires around it — re-introduces root ownership that the
        # PRIOR chown (which ran BEFORE this step) does not cover.  The model
        # captures the observed defect: this op leaves /workspace needing a
        # FOLLOWING ownership re-assertion regardless of the user it nominally
        # runs as.  Only a `chown -R vscode /workspace` issued AFTER this step
        # restores the durable invariant.  (Modelled below as a re-root in
        # `_update_workspace_path_ownership`, not a normal vscode-leaves-vscode
        # write, so the durable fix is observable.)
        return written

    def _update_workspace_path_ownership(
        self, container_name: str, command: list[str], user: str | None
    ) -> None:
        """Update the per-path ownership model for one exec (lead-mf15)."""
        owners = self._workspace_path_owner.setdefault(
            container_name,
            {p: "root" for p in self.AGENT_TOUCHED_PATHS},
        )

        # A recursive `chown -R vscode:vscode /workspace` re-owns ALL
        # agent-touched paths; a chown that names a specific path re-owns it.
        if command and command[0] == "chown":
            recursive = "-R" in command or "--recursive" in command
            spec_and_paths = [
                a for a in command[1:] if a not in ("-R", "--recursive")
            ]
            owner_spec = spec_and_paths[0] if spec_and_paths else ""
            paths = spec_and_paths[1:]
            target_user = owner_spec.split(":", 1)[0] if owner_spec else ""
            if target_user != AGENT_CONTAINER_USER:
                return
            covers_workspace = any(
                p.rstrip("/") == CONTAINER_WORKSPACE for p in paths
            )
            if recursive and covers_workspace:
                for p in self.AGENT_TOUCHED_PATHS:
                    owners[p] = AGENT_CONTAINER_USER
            else:
                for named in paths:
                    for p in self.AGENT_TOUCHED_PATHS:
                        if named.rstrip("/") == p:
                            owners[p] = AGENT_CONTAINER_USER
            return

        # lead-mf15 — the shop-templates refresh is the LAST provisioning op
        # and the observed mid-run re-root surface: a path it (or a
        # root-context op the container fires around it) creates/re-roots is
        # left ROOT-owned regardless of the user the refresh nominally runs
        # as.  Only a `chown -R vscode /workspace` issued AFTER it restores
        # vscode ownership.  Model that re-root explicitly so the durable fix
        # is observable in the agent-start snapshot.
        if command[:2] == ["shop-templates", "update"]:
            owners[CONTAINER_WORKSPACE] = "root"
            return

        # A content write under a path leaves that path owned by the running
        # user: root-context (user=None) re-roots it; vscode-context leaves it
        # vscode-owned.
        written = self._paths_written_under(command)
        if not written:
            return
        running_owner = user or "root"
        for p in written:
            owners[p] = running_owner

    def workspace_path_owners_at_agent_start(
        self, container_name: str
    ) -> dict[str, str]:
        """Ownership of each agent-touched workspace path at the instant the
        agent's tmux session is started (i.e. after container init).

        Empty until `tmux new-session` has been issued for the container.
        """
        return dict(
            self._workspace_path_owner_at_agent_start.get(container_name, {})
        )

    def root_owned_paths_at_agent_start(self, container_name: str) -> list[str]:
        """The agent-touched workspace paths still root-owned after container
        init — these are exactly the paths that would require a host-side
        chown.  Empty means the durable invariant holds (lead-mf15)."""
        owners = self.workspace_path_owners_at_agent_start(container_name)
        return [p for p, o in owners.items() if o != AGENT_CONTAINER_USER]

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
        self._container_env[container_name] = dict(env)
        self._container_mounts_full[container_name] = list(mounts)
        for key, val in env.items():
            cmd += ["-e", f"{key}={val}"]
            if key == "SHOPMSG_DSN":
                self._container_dsn[container_name] = val
            if key == "HTTPS_PROXY":
                self._container_proxy_env[container_name] = val
                self._container_broker[container_name] = val
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

    def messaging_db_reachable(
        self, dsn: str, container: str | None = None
    ) -> bool:
        """Reachable-by-default unless the DSN was marked unreachable.

        lead-cs7k: when ``container`` is supplied the probe runs from INSIDE
        that container's network (docker exec) rather than from the launcher
        host process; reachability is then resolved against the container's
        network so a target the launcher host cannot resolve still reads
        reachable when it is reachable from inside the product network.
        """
        self._probe_exec_contexts.append(("messaging_db", container))
        return self._probe_reachable(
            dsn, container, self._unreachable_dsns
        )

    def agent_vault_reachable(
        self, broker_address: str, container: str | None = None
    ) -> bool:
        """Reachable-by-default unless the broker was marked unreachable.

        lead-cs7k: ``container`` selects the inside-network probe context, as
        for ``messaging_db_reachable``.
        """
        self._probe_exec_contexts.append(("agent_vault", container))
        return self._probe_reachable(
            broker_address, container, self._unreachable_brokers
        )

    def _probe_reachable(
        self, addr: str, container: str | None, host_unreachable: set[str]
    ) -> bool:
        if not addr:
            return False
        host = self._target_host(addr)
        if container is not None:
            # Inside-network probe (docker exec): resolve against the
            # container's network reachable-target set.  A target registered
            # reachable-from-inside the container's network reaches even when
            # the launcher host cannot resolve it.
            network = self._container_network.get(container)
            reachable_inside = self._network_reachable_targets.get(network, set())
            if host in reachable_inside:
                return True
            # Otherwise fall back to the default-reachable model: only an
            # explicitly host-unreachable addr fails.
            return addr not in host_unreachable
        # Host-context probe (legacy): a target the launcher host cannot
        # resolve false-fails here, modelling the second-product bug.
        if host in self._host_unresolvable_targets:
            return False
        return addr not in host_unreachable

    def health_status(self, container_name: str) -> str:
        """Compose the container's health status.

        An explicit override (set_health_override) wins.  Otherwise the
        container is "healthy" only when beads is functionally usable inside
        it (a non-empty issue_prefix configured and not forced broken), the
        messaging database at its configured DSN is reachable, AND — when an
        agent-vault broker is configured for the container (ADR-026) — that
        broker is reachable; any other state is "unhealthy".  Returns "none"
        if the container is unknown.
        """
        if container_name in self._health_override:
            return self._health_override[container_name]
        if container_name not in self._all_containers:
            return "none"
        prefix = self._beads_prefix.get(container_name, "")
        beads_ok = bool(prefix) and container_name not in self._beads_broken
        dsn = self._container_dsn.get(container_name, "")
        db_ok = bool(dsn) and dsn not in self._unreachable_dsns
        broker = self._container_broker.get(container_name, "")
        # A configured-but-unreachable broker makes the container unhealthy
        # even when the process is alive (scenario 3b2a81c1).  When no broker
        # is configured for the container, the broker dimension is not gating
        # (preserves health scenarios that predate the agent-vault model).
        broker_ok = (not broker) or broker not in self._unreachable_brokers
        return "healthy" if (beads_ok and db_ok and broker_ok) else "unhealthy"

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
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess:
        self.exec_calls.append(
            ExecCall(
                container=container_name,
                command=command,
                user=user,
                env=dict(env) if env else None,
            )
        )
        prefix = ["docker", "exec"]
        if user is not None:
            prefix += ["-u", user]
        self._last_command = prefix + [container_name] + command

        # lead-mf15 — update the durable per-path ownership model for every
        # exec.  This runs BEFORE the command-specific simulations below so the
        # tmux new-session snapshot reflects the writes this same launch
        # sequence performed.
        self._update_workspace_path_ownership(container_name, command, user)

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
            # lead-mf15 — container init is complete; the agent is about to
            # engage.  Snapshot the ownership the vscode agent inherits so the
            # durable invariant ("no agent-touched path remains root-owned
            # after container init") can be asserted against this exact
            # moment.
            self._workspace_path_owner_at_agent_start[container_name] = dict(
                self._workspace_path_owner.get(container_name, {})
            )
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

        # Simulate `git -C <ws> show HEAD:.beads/issues.jsonl` (lead-rply →
        # lead-kjv7 DEFECT 1 RE-MODELLED).  The v0.2.7 fake served the
        # committed registry blob from `git show`, so the launcher's
        # prefix-detection read green even though it read from a path that is
        # EMPTY in a real container at that point.  Empirically `git show`
        # returned EMPTY and the launcher fell back to name-derivation
        # ("Configured beads issue_prefix 'scenarios' ... name-derived
        # fallback").  Correct the model: `git show HEAD:.beads/issues.jsonl`
        # returns EMPTY.  The committed prefix can ONLY be read from the
        # MATERIALIZED worktree file (see the `cat`/read handler below), and
        # ONLY after `git checkout HEAD -- .beads/issues.jsonl` has run.  A
        # launcher that reads the prefix from `git show` (the v0.2.7 shape)
        # therefore now reads EMPTY → name-derives → mismatches the committed
        # prefix, exactly the empirical DEFECT 1.
        if command[0] == "git" and "show" in command \
                and any(arg.endswith("HEAD:.beads/issues.jsonl") for arg in command):
            return subprocess.CompletedProcess(command, 0, "", "")

        # Simulate reading the MATERIALIZED committed registry from the working
        # tree (e.g. `cat /workspace/.beads/issues.jsonl`).  lead-kjv7 DEFECT 1:
        # the committed prefix MUST be read here — AFTER materialization — not
        # from `git show`.  Returns the committed-prefix blob ONLY once the
        # registry has been checked out into the worktree; before that the
        # worktree file is ABSENT (the real post-clone state), so the read
        # fails non-zero with an empty body.
        # Simulate `cat /workspace/.claude/shop/type.md` — the cloned shop's
        # canonical shop-type marker, read by the controller to derive the
        # `shop-templates update --shop-type <bc|lead>` value (lead-q5k7).
        # Returns the configured type ("bc" by default) so the refresh runs
        # with the type the shop was bootstrapped with.
        if command[0] == "cat" \
                and any(arg.endswith(".claude/shop/type.md") for arg in command):
            shop_type = self._shop_type.get(container_name, "bc")
            return subprocess.CompletedProcess(command, 0, shop_type + "\n", "")

        if command[0] == "cat" \
                and any(arg.endswith(".beads/issues.jsonl") for arg in command):
            if container_name not in self._beads_registry_materialized:
                return subprocess.CompletedProcess(
                    command, 1, "",
                    "cat: /workspace/.beads/issues.jsonl: No such file or "
                    "directory\n",
                )
            committed = self._committed_beads_prefix.get(container_name, "")
            if not committed:
                return subprocess.CompletedProcess(command, 0, "", "")
            blob = (
                '{"_type":"issue","id":"' + committed + '-eaa",'
                '"title":"seed","status":"open"}\n'
            )
            return subprocess.CompletedProcess(command, 0, blob, "")

        # Simulate `git -C <ws> checkout HEAD -- .beads/issues.jsonl`
        # (lead-rply) — materializes the committed registry into the working
        # tree.  Until this runs, the registry is ABSENT from the worktree.
        if command[0] == "git" and "checkout" in command \
                and any(arg.endswith(".beads/issues.jsonl") for arg in command):
            self._beads_registry_materialized.add(container_name)
            # lead-kjv7 DEFECT 3 — this write into `.beads` lands owned by the
            # running user (root by default), so a later vscode chown is
            # required to leave the tree usable by the agent.
            self._beads_owner[container_name] = user or "root"
            return subprocess.CompletedProcess(command, 0, "", "")

        # Simulate `bd bootstrap` — lead-ezzr, the PROVEN provisioning
        # mechanism.  On a fresh clone with committed `.beads/issues.jsonl`
        # MATERIALIZED in the worktree and NO pre-existing bd-created Dolt DB,
        # bootstrap imports the git-tracked JSONL, creates `embeddeddolt/`,
        # DERIVES the prefix from the imported registry, and yields
        # WRITE-READY.  If a bd-created Dolt DB ALREADY exists (a prior
        # `bd dolt pull` or `bd import` pre-created it), bootstrap is a NO-OP
        # ("database already exists, nothing to do") and leaves the BC WEDGED
        # — prefix unset, working set unprovisioned — modelling the
        # self-inflicted lead-vlsu deadlock.
        if is_bd_bootstrap_command(command):
            if container_name in self._beads_db_precreated:
                # Deadlock: a pre-existing bd-created DB makes bootstrap a
                # no-op.  Nothing is provisioned; the BC stays wedged.
                return subprocess.CompletedProcess(
                    command, 0,
                    "Bootstrap: database already exists, nothing to do\n",
                    "",
                )
            materialized = container_name in self._beads_registry_materialized
            committed = self._committed_beads_prefix.get(container_name, "")
            if committed and not materialized:
                # No git-tracked JSONL on disk to import.
                return subprocess.CompletedProcess(
                    command, 1, "",
                    "no JSONL to bootstrap from: .beads/issues.jsonl not "
                    "found\n",
                )
            # Bootstrap succeeds: import the committed registry, provision the
            # working set, create embeddeddolt/, and DERIVE the prefix from the
            # imported registry (no `bd config set` needed).
            self._beads_working_set_provisioned.add(container_name)
            self._beads_embeddeddolt_present.add(container_name)
            self._beads_db_precreated.add(container_name)
            if committed:
                self._beads_prefix[container_name] = committed
            # Bootstrap ran as the running user (root by default), so the
            # `.beads` tree it creates lands owned by that user; a later vscode
            # chown is required to leave the tree usable by the agent.
            self._beads_owner[container_name] = user or "root"
            return subprocess.CompletedProcess(
                command, 0,
                f"Imported issues from "
                f"{CONTAINER_WORKSPACE}/.beads/issues.jsonl\n",
                "",
            )

        # Simulate bd dolt pull — lead-ezzr revert-teeth.  This is the
        # SUPERSEDED mechanism's first step.  It pre-creates an EMPTY bd-created
        # Dolt DB but does NOT provision a usable working set or derive a
        # prefix.  Its side effect is the deadlock: a subsequent `bd bootstrap`
        # sees the pre-created DB and no-ops, leaving the BC wedged.
        if command[:3] == ["bd", "dolt", "pull"]:
            self._beads_db_precreated.add(container_name)
            self._beads_owner[container_name] = user or "root"
            return subprocess.CompletedProcess(command, 0, "", "")

        # Simulate `bd config set issue_prefix <prefix>` — the SUPERSEDED
        # prefix-adoption step (lead-ezzr: bd rejects it).  bd refuses to set
        # the prefix on a registry it manages, exiting non-zero; the prefix is
        # NOT recorded.  A launcher that relies on it therefore leaves the
        # prefix unset (revert-teeth).
        if command[:3] == ["bd", "config", "set"] and len(command) >= 5 \
                and command[3] == "issue_prefix":
            return subprocess.CompletedProcess(
                command, 1, "",
                "bd config set issue_prefix is not permitted: the prefix is "
                "derived from the registry\n",
            )

        # Simulate `bd import [<path>]` — the SUPERSEDED import step
        # (lead-ezzr).  It pre-creates the Dolt DB but does NOT derive a usable
        # prefix and does NOT leave the BC write-ready; worse, the pre-created
        # DB wedges a later `bd bootstrap` into a no-op.  So a launcher that
        # runs `bd import` to provision (the broken v0.2.7 / lead-kjv7 shape)
        # does NOT reach write-ready (revert-teeth).
        if command[:2] == ["bd", "import"]:
            self._beads_db_precreated.add(container_name)
            self._beads_embeddeddolt_present.add(container_name)
            self._beads_owner[container_name] = user or "root"
            return subprocess.CompletedProcess(
                command, 0, "",
                "imported into pre-created DB; prefix not derived\n",
            )

        # Simulate `bd create ...` — exits zero and emits a new issue id
        # carrying the configured prefix ONLY when beads is functionally
        # usable: a non-empty issue_prefix is configured, the Dolt working set
        # is provisioned, and beads is not forced broken.  Otherwise it exits
        # non-zero, mirroring the "database not initialized: issue_prefix
        # config is missing" failure.
        if command[:2] == ["bd", "create"]:
            prefix = self._beads_prefix.get(container_name, "")
            usable = (
                bool(prefix)
                and container_name in self._beads_working_set_provisioned
                and container_name not in self._beads_broken
            )
            if not usable:
                return subprocess.CompletedProcess(
                    command, 1, "",
                    "database not initialized: issue_prefix config is missing\n",
                )
            seq = self._beads_seq.get(container_name, 0) + 1
            self._beads_seq[container_name] = seq
            issue_id = f"{prefix}-{seq}"
            return subprocess.CompletedProcess(command, 0, f"{issue_id}\n", "")

        # Simulate `bd ready` — exits zero AND lists the committed issues when
        # beads is functionally usable (provisioned working set).  An empty /
        # unprovisioned working set fails the same way `bd create` does.
        if command[:2] == ["bd", "ready"]:
            prefix = self._beads_prefix.get(container_name, "")
            usable = (
                bool(prefix)
                and container_name in self._beads_working_set_provisioned
                and container_name not in self._beads_broken
            )
            if not usable:
                return subprocess.CompletedProcess(
                    command, 1, "",
                    "database not initialized: issue_prefix config is missing\n",
                )
            committed = self._committed_beads_prefix.get(container_name, "")
            listing = f"{committed}-eaa\tseed\n" if committed else ""
            return subprocess.CompletedProcess(command, 0, listing, "")

        # Simulate `shop-templates <subcommand> ...` — the launch
        # skill-refresh step (lead-dlrx scenario 75ae95be0ecf1640; lead-q5k7
        # bugfix).  This models the REAL bc-base CLI surface so a wrong
        # invocation cannot read green:
        #
        #   * VALID:  `shop-templates update --target <ws> --shop-type
        #             <bc|lead>` → exit 0, populates ".claude/skills/" with
        #             the (health-bearing) skill-group.  Only an update that
        #             TARGETS the workspace dir AND carries a recognised
        #             --shop-type populates it.
        #   * INVALID: any other subcommand — notably the old
        #             `pour`/`--workspace` shape — exits NON-ZERO with an
        #             argparse-style "invalid choice" stderr and deposits
        #             NOTHING (the real CLI rejects it).  This is the
        #             DEFECT-fidelity teeth: a launcher that execs `pour` (or
        #             omits/mistypes the flags) FAILS, and a controller that
        #             logs false success on that failure is caught.
        if command[:1] == ["shop-templates"]:
            self.refresh_calls.append(
                ExecCall(container=container_name, command=command, user=user)
            )
            subcommand = command[1] if len(command) > 1 else ""
            VALID_SUBCOMMANDS = {"list", "show", "bootstrap", "update"}
            if subcommand not in VALID_SUBCOMMANDS:
                return subprocess.CompletedProcess(
                    command, 2, "",
                    f"shop-templates: error: argument command: invalid "
                    f"choice: {subcommand!r} (choose from 'list', 'show', "
                    f"'bootstrap', 'update')\n",
                )
            if subcommand != "update":
                return subprocess.CompletedProcess(command, 0, "", "")
            # `update` — parse --target / --shop-type the way the real CLI
            # does.  `--workspace` is NOT a recognised flag for update; an
            # update that omits a valid --target therefore does NOT populate.
            target = None
            shop_type = None
            if "--target" in command:
                idx = command.index("--target")
                target = command[idx + 1] if idx + 1 < len(command) else None
            if "--shop-type" in command:
                idx = command.index("--shop-type")
                shop_type = command[idx + 1] if idx + 1 < len(command) else None
            if shop_type not in ("bc", "lead"):
                return subprocess.CompletedProcess(
                    command, 2, "",
                    "shop-templates update: error: argument --shop-type: "
                    f"expected one of 'bc', 'lead', got {shop_type!r}\n",
                )
            if container_name in self._skill_refresh_fails:
                # Criterion B failure surface — a valid invocation that the
                # package nonetheless rejects at runtime.  Deposits NOTHING.
                return subprocess.CompletedProcess(
                    command, 1, "",
                    "shop-templates update: failed to write skill-group "
                    "into target workspace\n",
                )
            if target == "/workspace":
                self._workspace_skills.setdefault(container_name, set()).update(
                    self.SHOP_TEMPLATES_SKILL_GROUP
                )
            return subprocess.CompletedProcess(command, 0, "", "")

        # Simulate `chown [-R] <user>:<group> <path...>` — lead-kjv7 DEFECT 3.
        # Ownership of `/workspace/.beads` is transferred to vscode ONLY when a
        # chown to vscode actually COVERS the `.beads` tree.  Two shapes cover
        # it: a RECURSIVE chown of /workspace (`chown -R vscode:vscode
        # /workspace`), or a chown that NAMES `.beads` directly.  A
        # NON-recursive chown of /workspace alone does NOT recurse into
        # `.beads` and so leaves it root-owned — the exact empirical DEFECT 3
        # ("/workspace chown did NOT cover/recurse .beads").
        if command and command[0] == "chown":
            recursive = "-R" in command or "--recursive" in command
            spec_and_paths = [a for a in command[1:] if a not in ("-R", "--recursive")]
            owner_spec = spec_and_paths[0] if spec_and_paths else ""
            paths = spec_and_paths[1:]
            target_user = owner_spec.split(":", 1)[0] if owner_spec else ""
            names_beads = any(".beads" in p for p in paths)
            covers_workspace = any(
                p.rstrip("/") == CONTAINER_WORKSPACE for p in paths
            )
            if target_user == AGENT_CONTAINER_USER and (
                names_beads or (recursive and covers_workspace)
            ):
                self._beads_owner[container_name] = AGENT_CONTAINER_USER
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

    def clone_exec_call(self, container_name: str) -> ExecCall | None:
        """Return the recorded launch-time `git clone` exec call (bclaunch-5fji).

        Returns the first ExecCall against ``container_name`` whose command is a
        ``git clone`` so tests can assert on the env (HTTPS_PROXY / GIT_SSL_CAINFO)
        the controller injected onto the clone exec.
        """
        for c in self.exec_calls:
            if c.container == container_name and c.command[:2] == ["git", "clone"]:
                return c
        return None
