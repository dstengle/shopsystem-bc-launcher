"""
FakeDockerDriver — in-memory test double for DockerDriver.

Records calls and returns pre-configured state.  Tests set up state before
running the controller under test, then assert on the recorded calls.
"""
from __future__ import annotations

import errno
import os
import re
import subprocess
from dataclasses import dataclass, field

from bc_launcher.driver import ContainerInfo, ContainerMount

# Linux MAX_ARG_STRLEN — the kernel's limit on the length of a SINGLE argv
# element (128 KiB), independent of ARG_MAX (~2 MiB for the whole argv+env).
# An execve whose single argument exceeds this fails with E2BIG
# ("Argument list too long"), which Python surfaces as OSError(errno.E2BIG).
# lead-m4zt models this boundary so a launcher that carries a >128 KiB content
# blob (def-bundle / startup-prompt) as one argv element fails here exactly as
# the real docker spawn does.  Modelled OPT-IN (see
# ``enforce_argv_strlen_limit``) so it is inert for every pre-existing scenario
# and only the oversized-bundle scenario arms it.
MAX_ARG_STRLEN = 128 * 1024

# Mirror the launcher's container-side constants so the fake can model
# `.beads` ownership transfer (lead-kjv7 DEFECT 3) without importing
# controller internals.
CONTAINER_WORKSPACE = "/workspace"
AGENT_CONTAINER_USER = "vscode"
# lead-z0v2 — the fixed container CA path (mirrors the controller constant);
# used by the CA-materialization filesystem model below.
AGENT_VAULT_CONTAINER_CA_PATH = "/home/vscode/.config/agent-vault/ca.pem"
# lead-a3kg — the poured "/workspace/.fabro/workflow.toml" the N4 fabro wiring
# reads in-container.  Its content matches the canonical def-source mirror the
# shop-templates pour delivers, so the fake serves that mirror's bytes when a
# test has not seeded distinctive poured content.
FABRO_WORKFLOW_TOML_CONTAINER_PATH = "/workspace/.fabro/workflow.toml"
_ASSET_WORKFLOW_TOML = (
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    + "/src/bc_launcher/assets/fabro-def/workflow.toml"
)
# lead-e5jx — the poured "/workspace/.fabro/dispatcher.toml" the reactive
# engage (`fabro run dispatcher.toml`) reads $BC_NAME from.  Its content
# matches the canonical def-source mirror the pour delivers when a test has not
# seeded distinctive poured content.
FABRO_DISPATCHER_TOML_CONTAINER_PATH = "/workspace/.fabro/dispatcher.toml"
_ASSET_DISPATCHER_TOML = (
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    + "/src/bc_launcher/assets/fabro-def/dispatcher.toml"
)


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


def _is_empty_remote_seed_command(command: list[str]) -> bool:
    """Return True if ``command`` is the launcher's empty-remote SEED step.

    lead-5k8c.  When `bd bootstrap` fails because the `<bc>-beads` Dolt remote
    is empty ("git remote has no branches"), the launcher INITIALIZES the
    remote by init-and-pushing an initial branch/commit then verifying
    `refs/dolt/*` appears in `git ls-remote`.  The seed runs as a login-shell
    script; it is recognised by its `bd dolt remote add` + `bd dolt push` +
    `git ls-remote ... refs/dolt` verification tail, distinct from the
    `bd bootstrap` step itself (which this matcher must NOT claim).
    """
    if (
        len(command) >= 3
        and command[0] == "bash"
        and command[1] in ("-lc", "-c")
    ):
        script = command[2]
        if "bd bootstrap" in script:
            return False
        return (
            "bd dolt push" in script
            and "ls-remote" in script
            and "refs/dolt" in script
        )
    return False


def _is_repo_create_command(command: list[str]) -> bool:
    """Return True if ``command`` is the launcher's ABSENT-repo CREATE step.

    lead-7jc2.  When `bd bootstrap` fails because the `<bc>-beads` GitHub
    tracker repo does not exist at all ("Repository not found"), the launcher
    CREATES it (`gh repo create <owner>/<bc>-beads` with an initial branch/
    commit) before seeding its Dolt remote.  The create step runs as a
    login-shell script and is recognised by its `gh repo create` body,
    distinct from the empty-remote seed step (which this matcher must NOT
    claim — the seed has no `gh repo create`).
    """
    if (
        len(command) >= 3
        and command[0] == "bash"
        and command[1] in ("-lc", "-c")
    ):
        return "gh repo create" in command[2]
    return False


def _is_origin_owner_writeback_command(command: list[str]) -> bool:
    """Return True if ``command`` is the launcher's ORIGIN_OWNER writeback step.

    lead-r34c / GAP B.  The BC's scaffolded beads tracker config is pushed with
    the literal ``ORIGIN_OWNER`` placeholder (correct at scaffold time — no
    origin owner is known yet).  BEFORE `bd bootstrap` runs, the in-container
    standup must RESOLVE that placeholder to the derived GitHub owner (parsed
    from the container's `/workspace` git origin) and WRITE it into BOTH the
    `.beads` config sync.remote AND the functional bd dolt remote, so the
    bootstrap clone target is `<owner>/<bc>-beads` and no `ORIGIN_OWNER`
    segment survives.  The writeback runs as a login-shell script and is
    recognised by its `remote get-url origin` owner-derivation combined with
    the `ORIGIN_OWNER` placeholder rewrite; distinct from the bd bootstrap,
    empty-remote seed, and absent-repo create steps (which this must NOT
    claim — none carries both tokens).
    """
    if (
        len(command) >= 3
        and command[0] == "bash"
        and command[1] in ("-lc", "-c")
    ):
        script = command[2]
        if "bd bootstrap" in script:
            return False
        return "remote get-url origin" in script and "ORIGIN_OWNER" in script
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
    # lead-m4zt: the payload streamed to the exec's STDIN (``docker exec -i``),
    # or None when nothing is piped in.  The content-placement fix carries the
    # def-bundle / oversized-prompt blob HERE — on STDIN, never on argv — so it
    # is immune to the MAX_ARG_STRLEN per-argument kernel limit.
    input: str | None = None
    # lead-lwk4 R7: True when the exec was issued DETACHED (``docker exec -d``),
    # so `subprocess.run` returns immediately without reading the exec's pipes.
    # The fabro ENGAGE is issued detached so `launch()` returns after starting
    # the foreground fabro server.
    detach: bool = False


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

        # lead-m4zt: when True, exec_run / run enforce the Linux MAX_ARG_STRLEN
        # per-single-argument limit (128 KiB) and RAISE OSError(errno.E2BIG,
        # "Argument list too long", "docker") when ANY single argv element
        # exceeds it — modelling the kernel refusing an over-long argument at
        # the docker spawn boundary.  A blob carried on STDIN (docker exec -i)
        # is NOT an argv element and never trips this.  Opt-in so it is inert
        # for every pre-existing scenario.
        self._enforce_arg_limit: bool = False

        # lead-m4zt: per-container tmux paste-buffer contents, loaded from the
        # exec's STDIN by `tmux load-buffer -` (the off-argv prompt-injection
        # path).  A subsequent `tmux paste-buffer` deposits this into the
        # agent's input buffer, and a discrete Enter commits it.
        self._tmux_loaded_buffer: dict[str, str] = {}

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

        # --- Scenario af2f03d3ac519cb5: local Docker image cache model ---
        # Maps an image reference (a moving tag like "repo:latest" OR a
        # content-addressable digest pin like "repo@sha256:...") to the DIGEST
        # CONTENT the local cache currently serves for that reference.  A tag's
        # cached entry is the digest the local cache holds under that tag (the
        # stale "D_old" when the registry has since republished "latest").  A
        # pull populates the digest-pinned reference so a run of that pin serves
        # the registry-current content.  Empty by default — only the freshness
        # scenario seeds it, so all other launch scenarios are unaffected.
        self._local_image_cache: dict[str, str] = {}
        # Ordered record of pull(image_ref) calls.
        self.pull_calls: list[str] = []
        # An optional RegistryDriver-like object the fake consults on pull to
        # fetch the registry-current digest into the local cache.  Set by the
        # freshness scenario via ``set_registry_for_pull``.
        self._registry_for_pull = None

        # Pane-marker simulation: list of (container_name, session, marker)
        # tuples that wait_for_pane_marker should treat as "never observed"
        # (i.e. simulate the timeout path).  Anything not listed is treated
        # as observed on the first poll (success path).
        self._marker_timeouts: set[tuple[str, str, str]] = set()

        # Record of wait_for_pane_marker invocations so tests can assert
        # exactly which markers the controller polled for and in what order.
        self.wait_for_marker_calls: list[tuple[str, str, str]] = []

        # lead-63em: recorded launch-diagnostic file writes.  Each entry is
        # (host_path, content).  The fake ALSO performs the real host write
        # (creating parent dirs) so a test can read the persisted file back
        # "from the host" without any tmux attach — exactly the property the
        # scenarios pin.
        self.launch_diagnostic_writes: list[tuple[str, str]] = []

        # lead-bnhn: when set, write_launch_diagnostic RAISES this OSError
        # instead of writing — modelling a non-writable diagnostic target dir
        # (the /var/lib/bc-launcher PermissionError crash).  The controller's
        # best-effort wrap must CATCH this so the launch is NOT aborted.  The
        # ATTEMPTED (host_path, content) is still recorded in
        # ``launch_diagnostic_writes`` so the test can assert the controller
        # tried to write to the documented path before the failure.
        self._launch_diagnostic_write_error: OSError | None = None

        # lead-63em: when True, EVERY agent-vault broker probe reports
        # unreachable regardless of address.  Used by the agent-vault
        # launch-failure scenario so the test need not re-derive the
        # product-slug-qualified broker host the controller resolves.
        self._all_brokers_unreachable: bool = False

        # --- lead-j351: slow brokered boot (delayed-marker) model ---
        # (container, session, marker) -> seconds-of-progressing-boot after
        # which the marker becomes observable.  Models a brokered boot that
        # reaches its input-ready marker only AFTER the legacy 60s deadline.
        # A fixed-60s-deadline wait would drop injection; a marker-keyed
        # (progress-based) wait keeps polling while the boot progresses and
        # still observes the marker.
        self._marker_delayed_after: dict[tuple[str, str, str], float] = {}
        # Records, per (container, session, marker), the simulated elapsed
        # seconds at which the marker was actually observed — so a test can
        # assert it was observed strictly after the legacy 60s deadline.
        self._marker_observed_at: dict[tuple[str, str, str], float] = {}
        # Monotonic operation index stamped by both wait_for_pane_marker and
        # exec_run, so tests can assert relative ORDER across the two surfaces
        # (e.g. input-ready wait precedes the prompt-injection send-keys).
        self._op_seq: int = 0
        # op index of the most recent input-ready-marker wait, per container.
        self._last_input_ready_wait_op: dict[str, int] = {}
        # op index of the send-keys carrying a given prompt text, per
        # (container, prompt-substring) — recorded on exec_run.
        self._prompt_sendkeys_op: dict[tuple[str, str], int] = {}

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

        # --- Blocking interactive option-screen model (lead-q3uy) ---
        # container_name -> {"content": str, "escapable": bool,
        #                    "dismissed": bool}.  When present and not yet
        # dismissed, the option screen BLOCKS the input prompt: capture_pane
        # returns its rendered content and any text send-keys is absorbed by
        # the screen (not buffered into the agent input).  A discrete Escape
        # send-keys dismisses an escapable screen; a non-escapable screen is
        # never dismissed by Escape.
        self._option_screen: dict[str, dict] = {}

        # --- Readiness-wait blocking interactive prompt model (lead-cw7m) ---
        # container_name -> {"content": str, "clears_on_escape": bool,
        #                    "dismissed": bool}.  Models a prompt presenting
        # DURING the readiness wait (BEFORE the input-ready marker), e.g. the
        # fullscreen-renderer onboarding prompt the new bc-base image renders
        # before the trust banner.  While present-and-undismissed:
        #   * wait_for_pane_marker(CLAUDE_INPUT_READY_MARKER) returns False
        #     (the prompt blocks reaching input-ready);
        #   * capture_pane returns the prompt's rendered content so the
        #     controller can classify and name it.
        # A discrete Escape send-keys DISMISSES the prompt; thereafter
        # (clears_on_escape=True) the input-ready marker becomes observable.
        # The never-clears variant (clears_on_escape=False) keeps presenting
        # the prompt no matter how many Escapes are sent, so the input-ready
        # marker is NEVER observed — exercising the bounded-timeout path.
        self._readiness_prompt: dict[str, dict] = {}

        # --- Self-advance readiness model (lead-gw9v / lead-c713) -----------
        # Models how the in-container agent runtime resolves the
        # workspace-trust gate during the INITIAL readiness wait, for the three
        # cases the lead-gw9v scenarios pin:
        #
        #   "self_advance" — bc-base bakes `bypassPermissionsModeAccepted`, so
        #       claude self-advances past the workspace-trust prompt straight to
        #       the input-ready marker "bypass permissions on".  The transient
        #       PRE-trust banner "Accessing workspace:" is NEVER caught by the
        #       launcher's polling (banner wait times out), but the pane is
        #       ALREADY at input-ready: capture_pane returns the input-ready
        #       marker and wait_for_pane_marker(input-ready) succeeds.  The
        #       launcher must treat the agent as up and SKIP the trust-accept
        #       Enter.
        #
        #   "pre_trust" — the agent first renders the transient banner
        #       "Accessing workspace:" (banner wait succeeds); the input-ready
        #       marker becomes observable only AFTER a trust-accept Enter is
        #       sent.  Until the trust-accept Enter is sent, neither the
        #       input-ready marker is observable nor does capture_pane show it.
        #
        #   "neither" — the agent comes up wedged: the banner is never observed
        #       (banner wait times out) AND the input-ready marker is never
        #       observed within the readiness timeout (input-ready wait times
        #       out, capture_pane never shows it).  The launcher must warn and
        #       abort non-zero WITHOUT injecting.
        #
        # container_name -> mode string.  Absent means "use the default model"
        # (both markers observable by default), preserving every pre-existing
        # launch scenario.
        self._self_advance_mode: dict[str, str] = {}
        # Per-container flag set once the input-ready marker has been observed
        # (via a successful input-ready marker wait OR a self-advance capture).
        # A bare-Enter send-keys issued BEFORE this flag is set, while a
        # self-advance mode is configured, is the trust-accept Enter (the
        # claude-launch keystream is a text+Enter call, not a bare Enter, and
        # the prompt-submit Enter arrives only AFTER input-ready is observed).
        self._input_ready_observed: set[str] = set()
        # Per-container count of trust-accept Enter keystrokes the launcher
        # sent — recognised as a bare Enter send-keys issued while the agent's
        # input buffer is EMPTY (the trust-accept Enter commits nothing; the
        # two-call submit's second Enter arrives with the prompt text buffered).
        # The self-advance scenario asserts this is ZERO (Enter SKIPPED); the
        # pre-trust scenario asserts it is >= 1 (Enter SENT).
        self._trust_accept_enter_count: dict[str, int] = {}
        # lead-cw7m — simulated monotonic clock backing self.monotonic(), used
        # by the controller's bounded readiness-wait scan-dismiss loop so the
        # never-clears bounded-timeout path terminates without real sleeping.
        self._sim_clock: float = 0.0
        # lead-gs03 — per-container record of send-keys payloads ABSORBED by a
        # present-and-undismissed blocking option screen.  Each entry is the
        # send-keys payload (target tokens stripped) the screen consumed while
        # it was present.  The tightened un-escapable scenario asserts this list
        # carries ZERO Enter-bearing invocations and ZERO keystrokes of any kind.
        self._keystrokes_while_screen_present: dict[str, list[list[str]]] = {}

        # --- lead-pixf: agent-presence model (f2ddd6c7 / aeebb281) ----------
        # Per-container flag set by ``set_agent_online`` to model a container
        # whose "agent" tmux session ALREADY holds a live claude process whose
        # ``shop-msg watch`` inbox watcher is armed.  ``agent_online`` reports
        # this for ``status`` (presence reporting) and ``start-agent`` (no-op
        # short-circuit).  Absent / False means offline (no live agent), which
        # is the default for every pre-existing scenario.
        self._agent_online_containers: set[str] = set()
        # Per-container count of `agent-vault run -- claude ...` launch
        # send-keys observed.  The start-agent no-op scenario (aeebb281)
        # asserts this stays ZERO for an already-live agent — i.e. NO second
        # claude process is started in the "agent" session.
        self._claude_launch_count: dict[str, int] = {}
        # lead-pixf: when present, list_bc_containers raises
        # DockerSocketUnreachableError (010e776c) instead of returning a list,
        # modelling an unreachable Docker daemon socket.  Carries the stderr
        # signature the real docker CLI emits.
        self._docker_socket_unreachable: str | None = None
        # lead-wdvx (Bug 1): the host docker socket's owning gid, as
        # `host_socket_gid` would resolve by stat-ing the host socket.  The
        # scenarios model gid 984.  None means the host socket cannot be
        # stat-ed (so the launcher adds no --group-add).
        self._host_socket_gid: int | None = None
        # lead-wdvx (Bug 1): per-container supplementary groups recorded from
        # `docker run --group-add <gid>` — i.e. what docker inspect's
        # HostConfig.GroupAdd would show for the launched container.
        self._container_group_add: dict[str, list[str]] = {}

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

        # --- lead-uiwu clone-path regression model -------------------------
        # FACET 1 (scn bdec2754d9135086 / 0b50d090c9cc3c45): the git-repo state
        # of /workspace and the remote it was cloned from.  A container is NOT a
        # git repo at /workspace until a `git clone <remote> /workspace` exec
        # succeeds; the remote it cloned from is recorded so the positive
        # scenario can assert /workspace was cloned from the manifest remote.
        self._workspace_cloned_from: dict[str, str] = {}
        # FACET 3 (scn 09f871cf8b99a34b, lead-z0v2 — supersedes retired
        # 0d29c76818a323a1): whether the broker MITM root CA has
        # been materialized into the container trust store.  The agent-vault-ca
        # materializer (entrypoint script) sets this; a clone routed through the
        # MITM proxy (clone_env carries an HTTPS_PROXY) FAILS TLS verification
        # ("unable to get local issuer certificate") UNLESS the CA was
        # materialized BEFORE the clone exec ran.
        self._broker_ca_materialized: set[str] = set()
        # Whether the CA was materialized BEFORE the (first) clone exec, so the
        # ordering teeth (CA-before-clone) are observable independently of the
        # clone's own pass/fail.
        self._ca_materialized_before_clone: dict[str, bool] = {}
        # Whether the AGENT_VAULT_CA_PEM env carries an inline PEM the
        # materializer can use (set from docker run -e).  The materializer is a
        # no-op when it is absent, modelling the real entrypoint guard.
        self._has_ca_pem: set[str] = set()
        # lead-z0v2 — a REAL per-container filesystem model for CA materialization,
        # so the test catches the actual regression (git pointed at a CA path
        # that was never written).  ``_container_files`` maps a container to a
        # {path: content} map of files the launcher's clone-prep actually wrote;
        # the clone simulation checks that the path git is configured to trust
        # (GIT_SSL_CAINFO on the clone exec) names a real, non-empty file whose
        # first line is "-----BEGIN CERTIFICATE-----".  A write-path-vs-trust-
        # path MISMATCH (the bug) therefore goes RED.  ``_av_ca_pem_value`` is
        # the inline PEM the operator supplied (None when empty — the real
        # flagless case).  ``_broker_ca_fetchable`` models whether
        # `agent-vault ca fetch` would succeed inside the container.
        self._container_files: dict[str, dict[str, str]] = {}
        self._av_ca_pem_value: dict[str, str] = {}
        self._broker_ca_fetchable: set[str] = set()

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
        # lead-ona9 — the workspace's "/workspace/.fabro/" fabro loop def,
        # modelled per container as a present/absent flag.  Delivered by the
        # SAME shop-templates pour that emits ".claude/skills/" (scenario
        # 7700eea079ffe1d8): a successful `shop-templates update --target
        # /workspace` emits it, exactly as it emits the skill-group.  Empty /
        # missing means the pour has NOT delivered it (e.g. a workspace-mount
        # launch that skips the pour, or a failed pour).
        self._workspace_fabro: dict[str, bool] = {}
        # lead-a3kg — the CONTENT of the poured/committed
        # "/workspace/.fabro/workflow.toml" as it stands INSIDE the container.
        # The N4 fabro-orchestrator wiring reads THIS file in-container (via a
        # `base64 <path>` exec), rewrites its BC_NAME/WORK_ID on the host, and
        # writes the result back — it no longer reads the retired baked host
        # asset.  Keyed per container; a test may seed distinctive content via
        # ``set_poured_workflow_toml`` (e.g. the bundle-default identity plus a
        # sentinel) so a rewrite derived from the CONTAINER file is
        # distinguishable from one derived from the host asset.  When unset, a
        # read of the poured file falls back to the canonical def-source mirror
        # bytes (what the shop-templates pour delivers).
        self._poured_workflow_toml: dict[str, str] = {}
        # lead-e5jx — the CONTENT of the poured/committed
        # "/workspace/.fabro/dispatcher.toml" as it stands INSIDE the
        # container.  The reactive-dispatcher engage (`fabro run
        # dispatcher.toml`) reads $BC_NAME from dispatcher.toml's
        # [run.environment.env] overlay, so the launcher must rewrite THIS
        # file's BC_NAME/WORK_ID to the launch identity too (not only
        # workflow.toml) or the reactive watcher runs against the bundle
        # default `fabro-throwaway`.  Same read/rewrite/write-back channel as
        # the poured workflow.toml; seed distinctive content via
        # ``set_poured_dispatcher_toml``.  When unset, a read falls back to the
        # canonical def-source mirror bytes the pour delivers.
        self._poured_dispatcher_toml: dict[str, str] = {}
        # The bc-base image's shop-type marker per container ("bc"/"lead"),
        # read by the controller from `.claude/shop/type.md`.  Defaults to
        # "bc"; tests may override via set_shop_type().
        self._shop_type: dict[str, str] = {}
        # --- lead-h755: runtime in-container tool-PATH model ---------------
        # The image each container was launched/placed on.
        self._container_image: dict[str, str] = {}
        # Per-container map of tool name -> absolute path resolvable on the
        # in-container PATH.  A `command -v <tool>` exec resolves against this.
        self._container_tool_path: dict[str, dict[str, str]] = {}
        # Per-container set of tools explicitly modelled ABSENT from PATH
        # (the teeth: a `command -v <tool>` for an absent tool exits non-zero).
        self._container_tool_absent: dict[str, set[str]] = {}
        # lead-ckq5: per-container fabro version the in-container
        # `fabro --version` exec reports (models the OUT-OF-BAND live binary
        # invocation the lead's pull verification exercises).
        self._container_fabro_version: dict[str, str] = {}
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

        # --- lead-5k8c empty beads-dolt-remote model -----------------------
        # Containers whose `<bc>-beads` Dolt remote is EMPTY/uninitialized.
        # While empty, a `bd bootstrap` clone fails with "git remote has no
        # branches: ...; initialize the repository with an initial
        # branch/commit first" — the exact strand-class condition observed
        # live 2026-06-22.  The launcher's empty-remote provisioning seeds the
        # remote (init-and-push an initial branch/commit), after which the
        # container is recorded as seeded and bootstrap succeeds.
        self._beads_remote_empty: set[str] = set()
        # lead-ypnz / GAP D — per-container OVERRIDE for the exact error text an
        # EMPTY/unseeded `<bc>-beads` remote makes `bd bootstrap`'s clone fail
        # with.  Absent means the legacy hardcoded "git remote has no branches"
        # text.  This lets a scenario model the CURRENT bc-base dolt clone
        # failure ("clone failed; remote at that url contains no Dolt data")
        # that a freshly `gh repo create --add-readme`'d tracker (git README
        # branch present, NO dolt refs) produces — so the empty-remote-seed
        # classifier is exercised over BOTH the current and legacy error texts.
        self._beads_remote_empty_error: dict[str, str] = {}
        # Containers whose previously-empty beads remote has been SEEDED by the
        # launcher's empty-remote init-and-push step.
        self._beads_remote_seeded: set[str] = set()
        # Whether even the launcher's empty-remote seed step itself fails
        # (models a seed that cannot reach/initialize the remote), so the
        # warn-and-continue-to-agent-start path can be exercised.
        self._beads_remote_seed_fails: set[str] = set()
        # lead-7jc2 — containers whose `<bc>-beads` GitHub tracker repo does
        # NOT EXIST at all.  While absent (and not yet created by the launcher)
        # a `bd bootstrap` clone fails "Repository not found" — a strictly
        # earlier failure than the empty-but-existing remote's "git remote has
        # no branches".  The launcher must CREATE the absent repo before it can
        # seed it.
        self._beads_repo_absent: set[str] = set()
        # Containers whose previously-absent `<bc>-beads` tracker repo has been
        # CREATED by the launcher's absent-repo provisioning step.
        self._beads_repo_created: set[str] = set()
        # lead-r34c / GAP B — containers whose scaffolded functional bd dolt
        # remote still carries the literal ORIGIN_OWNER placeholder (pushed at
        # scaffold time when no origin owner was known).  While the placeholder
        # survives, `bd dolt remote list` reports an ORIGIN_OWNER owner segment
        # and `bd bootstrap`'s clone target is ORIGIN_OWNER/<bc>-beads, failing
        # "Repository not found".  The launcher's resolve-and-writeback step
        # rewrites the functional remote to the derived owner before bootstrap.
        self._beads_remote_owner_placeholder: set[str] = set()
        # The GitHub owner the container's /workspace git origin resolves to
        # (models `git -C /workspace remote get-url origin`); the writeback step
        # derives the functional remote's owner segment from it.
        self._container_origin_owner: dict[str, str] = {}
        # The owner segment currently on the functional bd dolt remote (what
        # `bd dolt remote list` reports).  Set by the writeback step to the
        # derived owner once ORIGIN_OWNER has been resolved.
        self._beads_functional_remote_owner: dict[str, str] = {}

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

    def enforce_argv_strlen_limit(self, enforce: bool = True) -> None:
        """lead-m4zt: arm the Linux MAX_ARG_STRLEN per-argument kernel limit.

        Once armed, ``exec_run`` / ``run`` raise
        ``OSError(errno.E2BIG, "Argument list too long", "docker")`` when ANY
        single argv element exceeds 128 KiB — exactly as the real docker spawn
        does when a launcher carries an oversized content blob (def-bundle /
        startup-prompt) as one argv element.  A blob streamed on STDIN
        (``docker exec -i``) is not an argv element and never trips this.
        """
        self._enforce_arg_limit = enforce

    def _assert_within_arg_strlen(self, argv: list[str]) -> None:
        """Raise E2BIG if any single argv element exceeds MAX_ARG_STRLEN.

        No-op unless ``enforce_argv_strlen_limit`` armed the limit.  Modelled
        on the kernel's per-argument check: execve inspects EACH argument's
        length and fails the WHOLE spawn with E2BIG if any one is too long,
        independent of the total env size.
        """
        if not self._enforce_arg_limit:
            return
        for elem in argv:
            if len(str(elem).encode("utf-8", "surrogatepass")) > MAX_ARG_STRLEN:
                raise OSError(
                    errno.E2BIG, os.strerror(errno.E2BIG), "docker"
                )

    # --- lead-h755: runtime in-container tool-PATH model -------------------
    # A launched bc-base BC ALREADY has gh and agent-vault resolvable on PATH
    # at runtime ("command -v gh" / "command -v agent-vault" exit zero inside
    # the running container).  This dispatch PINS that runtime invariant.  The
    # model represents, per container, the executables resolvable on the
    # in-container PATH (tool name -> absolute path).  A `command -v <tool>`
    # exec inside the container returns exit 0 + the path when the tool is
    # resolvable, or exit 1 + empty output when it is NOT — so the model can
    # FAITHFULLY REPRESENT ABSENCE (the teeth: removing gh or agent-vault from
    # the modelled PATH must drive the scenario RED).
    #
    # docker is EXPLICITLY EXCLUDED from the bc-base PATH (PDR-020 Addendum II;
    # docker is bc-LEAD-only).  When a container is placed on the bc-base image
    # the default modelled PATH carries gh + agent-vault (plus the other baked
    # framework CLIs) but NOT docker — so a `command -v docker` would exit
    # non-zero, matching the real bc-base image.
    # lead-ckq5: fabro (baked binary, pinned v0.254.0 from fabro-sh/fabro) and
    # anthropic-oauth-shim (a real stdlib-only launcher COPIED onto PATH) are
    # ALSO baked bc-base tools, so a container placed on the bc-base image
    # resolves them on PATH; removing either from this map drives the
    # a3512aedb8763150 runtime leg RED (faithful absence).
    _BC_BASE_DEFAULT_PATH_TOOLS = {
        "gh": "/usr/bin/gh",
        "agent-vault": "/usr/local/bin/agent-vault",
        "shop-msg": "/usr/local/bin/shop-msg",
        "shop-templates": "/usr/local/bin/shop-templates",
        "bc-container": "/usr/local/bin/bc-container",
        "bd": "/usr/local/bin/bd",
        "fabro": "/usr/local/bin/fabro",
        "anthropic-oauth-shim": "/usr/local/bin/anthropic-oauth-shim",
    }

    # lead-ckq5: the fabro version the baked binary reports inside the running
    # container. This is the model of the OUT-OF-BAND live `fabro --version`
    # (the binary can't run in-env); the a3512aedb8763150 runtime leg is bound
    # to the Dockerfile install pin by the conftest step, and this seed lets the
    # in-container exec model report the pinned version faithfully. It is
    # DERIVED from the Dockerfile FABRO_VERSION pin at seed time so the two
    # cannot silently drift.
    _BC_BASE_FABRO_VERSION = "v0.254.0"

    def set_running_on_bc_base_image(
        self, container_name: str, image: str
    ) -> None:
        """Mark ``container_name`` running on the pinned bc-base ``image``.

        Seeds the in-container PATH with the bc-base baked tool set (gh +
        agent-vault among them); docker is deliberately NOT seeded (bc-base
        carries no docker CLI by design)."""
        self.set_running(container_name, True)
        self._container_image[container_name] = image
        # Only seed defaults the test has not already overridden, so an
        # absence override applied before this call survives.
        seeded = self._container_tool_path.setdefault(container_name, {})
        for tool, path in self._BC_BASE_DEFAULT_PATH_TOOLS.items():
            seeded.setdefault(tool, path)
        # lead-ckq5: seed the fabro version the baked binary reports. Default to
        # the module constant; a test may override via set_container_fabro_version
        # to drive the a3512aedb8763150 version-mismatch teeth RED.
        self._container_fabro_version.setdefault(
            container_name, self._BC_BASE_FABRO_VERSION
        )

    def set_container_fabro_version(
        self, container_name: str, version: str | None
    ) -> None:
        """lead-ckq5: override the fabro version the in-container
        `fabro --version` exec reports (or, with ``None``, model fabro as absent
        so the exec exits non-zero) — the teeth for the a3512aedb8763150 fabro
        leg."""
        if version is None:
            self._container_fabro_version.pop(container_name, None)
            self.set_container_tool_absent(container_name, "fabro")
        else:
            self._container_fabro_version[container_name] = version

    def set_container_tool_absent(
        self, container_name: str, tool: str
    ) -> None:
        """Model ``tool`` as NOT resolvable on the container's in-container
        PATH (drives the regression-guard scenario RED)."""
        self._container_tool_path.setdefault(container_name, {}).pop(tool, None)
        self._container_tool_absent.setdefault(container_name, set()).add(tool)

    def container_image(self, container_name: str) -> str:
        """Return the image the container was launched/placed on, or ""."""
        return self._container_image.get(container_name, "")

    def add_tmux_session(self, container_name: str, session_name: str) -> None:
        self._tmux_sessions.setdefault(container_name, set()).add(session_name)

    def set_agent_online(self, container_name: str, online: bool = True) -> None:
        """lead-pixf: model a container whose "agent" tmux session holds a
        LIVE claude process whose ``shop-msg watch`` watcher is armed.

        Used by f2ddd6c7 (status reports presence "online") and aeebb281
        (start-agent no-ops against an already-live agent).  Setting this
        ALSO ensures the "agent" tmux session exists, since a live agent
        implies the session is present.
        """
        if online:
            self._agent_online_containers.add(container_name)
            self.add_tmux_session(container_name, "agent")
        else:
            self._agent_online_containers.discard(container_name)

    def set_docker_socket_unreachable(
        self,
        unreachable: bool = True,
        stderr: str = (
            "Cannot connect to the Docker daemon at "
            "unix:///var/run/docker.sock. Is the docker daemon running?"
        ),
    ) -> None:
        """lead-pixf (010e776c): make ``list_bc_containers`` raise
        ``DockerSocketUnreachableError`` (modelling an unreachable Docker
        daemon socket) instead of returning a container list."""
        self._docker_socket_unreachable = stderr if unreachable else None

    # lead-wdvx (Bug 2): canned docker CLI stderr signatures the real docker
    # CLI emits for the two CONFIG faults the bugfix must classify as
    # docker-unreachable (distinct from daemon-down).
    # NOTE (lead-wdvx teeth): these are deliberately the TERSE real docker CLI
    # forms that the daemon-DOWN-only classifier genuinely MISSES — the
    # permission-denied line carries no "daemon" token and the not-mounted line
    # is a bare "no such file or directory" against the socket path with no
    # "cannot connect to the docker daemon" phrasing.  A faithful classifier
    # must match these on the permission-denied / no-such-file signatures the
    # bugfix adds; the old daemon-down-only matcher does NOT, so reverting the
    # classifier turns the Bug 2 scenarios RED (verified teeth).
    PERMISSION_DENIED_STDERR = (
        "Got permission denied while trying to connect to the Docker socket "
        "at unix:///var/run/docker.sock: dial unix /var/run/docker.sock: "
        "connect: permission denied"
    )
    NOT_MOUNTED_STDERR = (
        "error during connect: Get "
        "\"http://%2Fvar%2Frun%2Fdocker.sock/v1.45/containers/json\": "
        "open /var/run/docker.sock: no such file or directory"
    )

    def set_docker_socket_permission_denied(self, denied: bool = True) -> None:
        """lead-wdvx (Bug 2): model the socket as MOUNTED but the calling user
        denied access (permission-denied).  Docker-dependent driver calls
        (`is_running`, `list_bc_containers`) raise
        ``DockerSocketUnreachableError`` carrying the permission-denied stderr
        signature, modelling the real docker CLI's behaviour."""
        self._docker_socket_unreachable = (
            self.PERMISSION_DENIED_STDERR if denied else None
        )

    def set_docker_socket_not_mounted(self, not_mounted: bool = True) -> None:
        """lead-wdvx (Bug 2): model the docker socket as NOT mounted into the
        calling environment.  Docker-dependent driver calls raise
        ``DockerSocketUnreachableError`` carrying the not-mounted stderr
        signature (a "no such file or directory" against the socket path)."""
        self._docker_socket_unreachable = (
            self.NOT_MOUNTED_STDERR if not_mounted else None
        )

    def set_host_socket_gid(self, gid: int | None) -> None:
        """lead-wdvx (Bug 1): model the owning gid of the host docker socket
        (what `host_socket_gid` resolves by stat-ing the host socket).  The
        scenarios model gid 984."""
        self._host_socket_gid = gid

    def host_socket_gid(self, socket_path: str) -> int | None:
        """lead-wdvx (Bug 1): return the configured host docker socket gid."""
        return self._host_socket_gid

    def container_group_add(self, container_name: str) -> list[str]:
        """lead-wdvx (Bug 1): the supplementary groups the launcher granted
        the container (`docker run --group-add`), i.e. docker inspect's
        HostConfig.GroupAdd."""
        return list(self._container_group_add.get(container_name, []))

    def non_root_docker_call_permission_denied(
        self, container_name: str, host_socket_gid: int
    ) -> bool:
        """lead-wdvx (Bug 1): model whether a docker call made by the
        container's NON-ROOT default user against the mounted socket is
        rejected permission-denied.

        Faithful semantics: a non-root user can use the mounted socket ONLY
        when the host socket's owning gid is among the container's
        supplementary groups (the `--group-add <host-socket-gid>` the launcher
        must grant).  Absent that group, the non-root user is outside the
        socket's group and every docker call is permission-denied — exactly
        the masked fault the bugfix repairs.
        """
        return str(host_socket_gid) not in self.container_group_add(
            container_name
        )

    def claude_launch_count(self, container_name: str) -> int:
        """lead-pixf (aeebb281): how many `agent-vault run -- claude ...`
        launch send-keys were issued against this container — i.e. how many
        claude agent processes the launcher tried to start in its session."""
        return self._claude_launch_count.get(container_name, 0)

    def set_tmux_pane_content(self, container_name: str, content: str) -> None:
        self._tmux_pane[container_name] = content

    def simulate_option_screen(
        self,
        container_name: str,
        content: str,
        *,
        escapable: bool,
    ) -> None:
        """Model a blocking interactive option screen present on engage.

        lead-q3uy — after the input-ready marker but before the startup prompt
        is submitted, the agent runtime presents a blocking interactive option
        screen.  The launcher reads the rendered pane via ``capture_pane`` to
        classify it.  ``content`` is the rendered screen text the launcher
        captures; ``escapable`` records whether the screen advertises an
        Escape/dismiss affordance.

        Faithful submit semantics: while the option screen is present it
        BLOCKS the input prompt, so any text send-keys is absorbed by the
        screen rather than landing in the agent's input buffer.  A discrete
        ``Escape`` send-keys DISMISSES an escapable screen (after which the
        input prompt is reachable again and a text+Enter submit commits as
        normal).  A non-escapable screen is NOT dismissed by Escape.
        """
        self._option_screen[container_name] = {
            "content": content,
            "escapable": escapable,
            "dismissed": False,
        }
        # lead-gs03 — the send-keys RECORDER for keystrokes the screen absorbs.
        # While a blocking option screen is present-and-undismissed the screen
        # intercepts any keystream so it never lands in the agent input buffer.
        # The PRIOR model swallowed those keystrokes' EFFECT silently, which let
        # a phantom Enter against an un-escapable screen pass undetected (the
        # step only inspected the input buffer, not the keys that were sent).
        # Record every send-keys payload absorbed while the screen is present so
        # the tightened scenario can assert ZERO Enter / ZERO keystrokes of any
        # kind reached the screen — the absorbed invocation is RECORDED, not
        # silently dropped.
        self._keystrokes_while_screen_present.setdefault(container_name, [])

    def simulate_readiness_wait_prompt(
        self,
        container_name: str,
        content: str,
        *,
        clears_on_escape: bool = True,
    ) -> None:
        """Model a blocking interactive prompt presenting DURING the readiness
        wait, BEFORE the input-ready marker (lead-cw7m / lead-c713).

        This is the readiness-wait-phase analogue of ``simulate_option_screen``
        (which models the ENGAGE phase, AFTER input-ready).  While the prompt
        is present and not yet dismissed:

          * ``wait_for_pane_marker(CLAUDE_INPUT_READY_MARKER)`` returns False —
            the prompt blocks the agent from reaching the input-ready marker;
          * ``capture_pane`` returns ``content`` so the controller can detect,
            classify, and NAME the prompt.

        A discrete ``Escape`` send-keys DISMISSES the prompt.  When
        ``clears_on_escape`` is True (the default), once dismissed the
        input-ready marker becomes observable on the next wait and the launch
        proceeds to inject.  When ``clears_on_escape`` is False (the
        never-clears variant), the prompt keeps re-presenting after every
        Escape, so the input-ready marker is NEVER observed — exercising the
        bounded-timeout path (the controller must STOP dismissing at 60s and
        proceed WITHOUT injecting, rather than looping indefinitely).
        """
        self._readiness_prompt[container_name] = {
            "content": content,
            "clears_on_escape": clears_on_escape,
            "dismissed": False,
            "escape_count": 0,
        }

    def simulate_self_advance_readiness(
        self, container_name: str, mode: str
    ) -> None:
        """Model how the agent resolves the workspace-trust gate during the
        INITIAL readiness wait (lead-gw9v).

        ``mode`` is one of:
          * "self_advance" — claude self-advanced past trust straight to the
            input-ready marker; the transient banner is never caught.
          * "pre_trust" — the transient trust banner is rendered first; the
            input-ready marker becomes observable only after a trust-accept
            Enter is sent.
          * "neither" — neither marker is reached within the readiness timeout.

        See the ``_self_advance_mode`` field doc for the full per-mode
        wait/capture semantics this drives.
        """
        assert mode in ("self_advance", "pre_trust", "neither"), mode
        self._self_advance_mode[container_name] = mode
        self._trust_accept_enter_count.setdefault(container_name, 0)

    def trust_accept_enter_count(self, container_name: str) -> int:
        """How many trust-accept Enter keystrokes the launcher sent during the
        readiness wait (lead-gw9v).

        Recognised as a bare Enter send-keys issued while the agent input
        buffer is empty (the trust-accept Enter commits nothing).  ZERO in the
        self-advance case (Enter SKIPPED); >= 1 in the pre-trust case (Enter
        SENT).
        """
        return self._trust_accept_enter_count.get(container_name, 0)

    def monotonic(self) -> float:
        """Deterministic, strictly-advancing monotonic clock for tests
        (lead-cw7m).

        Each call advances the simulated clock by
        ``READINESS_DISMISS_POLL_SECONDS`` so the controller's bounded
        readiness-wait scan-dismiss loop budgets its TOTAL elapsed time
        against simulated (not wall-clock) time: the never-clears
        bounded-timeout path terminates after a FINITE number of iterations
        (~60s / per-attempt budget) with NO real sleeping, while the happy
        path (which breaks on the first input-ready observation) is unaffected.
        """
        from bc_launcher.controller import READINESS_DISMISS_POLL_SECONDS
        self._sim_clock += READINESS_DISMISS_POLL_SECONDS
        return self._sim_clock

    def readiness_prompt_escape_count(self, container_name: str) -> int:
        """How many discrete Escape send-keys the readiness-wait prompt has
        absorbed (lead-cw7m).  Tests assert this is >= 1 (the prompt WAS
        Esc-dismissed) and, for the never-clears bounded path, that it is a
        FINITE small number (the loop terminated, did not spin indefinitely).
        """
        rp = self._readiness_prompt.get(container_name)
        return rp["escape_count"] if rp else 0

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

    def set_all_brokers_unreachable(self, unreachable: bool = True) -> None:
        """lead-63em: make EVERY agent-vault broker probe report unreachable.

        Lets the agent-vault launch-failure scenario fail the agent-vault
        readiness barrier without re-deriving the product-slug-qualified
        broker host the controller resolves at launch.
        """
        self._all_brokers_unreachable = unreachable

    def write_launch_diagnostic(self, host_path: str, content: str) -> None:
        """Persist a launch-failure diagnostic to a real host file (lead-63em).

        Records the (host_path, content) call AND performs the actual host
        write — creating parent dirs — so a test can read the persisted file
        back from the host filesystem with no container / tmux involved,
        modelling the real host write.  Tests point
        ``BCLAUNCHER_HOST_STATE_DIR`` at a tmp dir so the write lands under
        the test sandbox.
        """
        from pathlib import Path as _Path
        self.launch_diagnostic_writes.append((host_path, content))
        # lead-bnhn: model a non-writable target dir — raise BEFORE any real
        # write so the controller's best-effort wrap is exercised end-to-end.
        if self._launch_diagnostic_write_error is not None:
            raise self._launch_diagnostic_write_error
        p = _Path(host_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def fail_launch_diagnostic_write(
        self, error: OSError | None = None
    ) -> None:
        """Force ``write_launch_diagnostic`` to RAISE (lead-bnhn).

        Models a non-writable diagnostic target directory — e.g. the
        root-owned ``/var/lib/bc-launcher`` PermissionError the fresh-adopter
        operator E2E hit.  ``error`` defaults to a ``PermissionError`` whose
        message mirrors the real ``[Errno 13] Permission denied`` so the
        controller's host-discoverable warning carries a realistic cause.  The
        controller's best-effort wrap MUST catch this so the launch is not
        aborted; re-raising it (fatal) is the RED teeth for the non-fatal pin.
        """
        if error is None:
            error = PermissionError(
                13, "Permission denied"
            )
        self._launch_diagnostic_write_error = error

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

    def set_beads_remote_empty(
        self, container_name: str, empty: bool = True
    ) -> None:
        """Model the BC's `<bc>-beads` Dolt remote as EMPTY/uninitialized
        (lead-5k8c).  While empty (and not yet seeded by the launcher), a
        `bd bootstrap` clone fails with "git remote has no branches".
        """
        if empty:
            self._beads_remote_empty.add(container_name)
        else:
            self._beads_remote_empty.discard(container_name)

    def set_beads_bootstrap_error(
        self, container_name: str, error_text: str
    ) -> None:
        """lead-ypnz / GAP D — OVERRIDE the exact error text an EMPTY/unseeded
        `<bc>-beads` remote makes `bd bootstrap`'s clone fail with.

        Models the CURRENT bc-base dolt clone failure for a freshly
        `gh repo create --add-readme`'d tracker (git README branch present, NO
        dolt refs): "clone failed; remote at that url contains no Dolt data".
        The empty-remote-seed classifier the controller gates its seed step on
        (`_is_empty_remote_failure`) must recognise this current text in
        addition to the legacy "git remote has no branches" text, so the seed
        fires and the retried `bd bootstrap` exits zero.  Setting this also
        marks the container's remote EMPTY so the failure is produced.
        """
        self._beads_remote_empty.add(container_name)
        self._beads_remote_empty_error[container_name] = error_text

    def set_beads_remote_seed_fails(
        self, container_name: str, fails: bool = True
    ) -> None:
        """Model the launcher's empty-remote SEED step itself failing
        (lead-5k8c), so the warn-and-continue-to-agent-start path can be
        pinned: a seed that cannot initialize the remote must NOT abort.
        """
        if fails:
            self._beads_remote_seed_fails.add(container_name)
        else:
            self._beads_remote_seed_fails.discard(container_name)

    def beads_remote_seeded(self, container_name: str) -> bool:
        """True once the launcher's empty-remote init-and-push step has seeded
        the previously-empty `<bc>-beads` Dolt remote (lead-5k8c)."""
        return container_name in self._beads_remote_seeded

    def set_beads_repo_absent(
        self, container_name: str, absent: bool = True
    ) -> None:
        """Model the BC's `<bc>-beads` GitHub tracker repo as NOT EXISTING
        (lead-7jc2).  While absent (and not yet created by the launcher), a
        `bd bootstrap` clone fails "Repository not found" — the launcher must
        CREATE the repo (with an initial branch/commit) before it can seed the
        Dolt remote and provision the working set.
        """
        if absent:
            self._beads_repo_absent.add(container_name)
        else:
            self._beads_repo_absent.discard(container_name)

    def beads_repo_created(self, container_name: str) -> bool:
        """True once the launcher's absent-repo provisioning step has CREATED
        the previously-absent `<bc>-beads` tracker repo (lead-7jc2)."""
        return container_name in self._beads_repo_created

    def set_beads_remote_owner_placeholder(
        self, container_name: str, origin_owner: str
    ) -> None:
        """Model the BC's scaffolded functional bd dolt remote carrying the
        literal ORIGIN_OWNER placeholder (lead-r34c / GAP B).

        Pushed at scaffold time when no origin owner was known yet.  While the
        placeholder survives, `bd dolt remote list` reports an ORIGIN_OWNER
        owner segment and `bd bootstrap`'s clone target is
        ORIGIN_OWNER/<bc>-beads (fails "Repository not found").  ``origin_owner``
        is the owner the container's /workspace git origin resolves to — the
        launcher's resolve-and-writeback step must derive it and rewrite the
        functional remote to it before bootstrap.
        """
        self._beads_remote_owner_placeholder.add(container_name)
        self._container_origin_owner[container_name] = origin_owner
        self._beads_functional_remote_owner[container_name] = "ORIGIN_OWNER"

    def beads_functional_remote_owner(self, container_name: str) -> str:
        """The owner segment currently on the functional bd dolt remote — what
        `bd dolt remote list` reports and `bd bootstrap` clones from (lead-r34c
        / GAP B).  "ORIGIN_OWNER" until the launcher's writeback step resolves
        it to the derived owner."""
        return self._beads_functional_remote_owner.get(container_name, "")

    def beads_functional_remote_url(self, container_name: str) -> str:
        """The full functional bd dolt remote URL `bd dolt remote list` reports
        (lead-r34c / GAP B), built from the current owner segment."""
        owner = self._beads_functional_remote_owner.get(container_name, "")
        if not owner:
            return ""
        return f"git+https://github.com/{owner}/{container_name}-beads.git"

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

    # --- lead-uiwu: clone-path regression accessors ------------------------

    def workspace_is_git_repo(self, container_name: str) -> bool:
        """Whether /workspace is a git repository (a clone succeeded into it).

        lead-uiwu FACET 1.  False after a SILENT empty launch (no clone) and
        after a clone that failed; True only once a `git clone <remote>
        /workspace` exec succeeded.
        """
        return container_name in self._workspace_cloned_from

    def workspace_cloned_from(self, container_name: str) -> str | None:
        """The remote URL /workspace was cloned from, or None (lead-uiwu)."""
        return self._workspace_cloned_from.get(container_name)

    def workspace_owner(self, container_name: str) -> str:
        """Owner of the /workspace directory (lead-uiwu FACET 2).

        Defaults to "root" (the image WORKDIR default); becomes "vscode" once a
        chown to the agent user covers /workspace.
        """
        return self._workspace_path_owner.get(container_name, {}).get(
            CONTAINER_WORKSPACE, "root"
        )

    def broker_ca_materialized(self, container_name: str) -> bool:
        """Whether the broker MITM root CA is in the trust store (FACET 3)."""
        return container_name in self._broker_ca_materialized

    def ca_materialized_before_clone(self, container_name: str) -> bool:
        """Whether the broker CA was materialized BEFORE the clone (FACET 3)."""
        return self._ca_materialized_before_clone.get(container_name, False)

    # --- lead-z0v2: real container CA filesystem model ---------------------

    _DEFAULT_FAKE_FETCHED_CA = (
        "-----BEGIN CERTIFICATE-----\n"
        "MIIB/fake/agent-vault/broker/root/CA/fetched/via/ca/fetch\n"
        "-----END CERTIFICATE-----\n"
    )

    def set_broker_ca_fetchable(self, container_name: str) -> None:
        """Mark a container so an in-container `agent-vault ca fetch` succeeds.

        lead-z0v2 — models the working operator path: a running broker from
        which the launcher's clone-prep can fetch the CA when no inline
        AGENT_VAULT_CA_PEM was supplied.
        """
        self._broker_ca_fetchable.add(container_name)

    @staticmethod
    def _extract_ca_path_from_prep(script_body: str) -> str:
        """Extract the CA target path the clone-prep script writes to.

        The controller's script assigns ``ca="<path>"``; recover that literal
        so the simulated write lands at the SAME path the script writes to (and
        thus the SAME path GIT_SSL_CAINFO is set to) — that path-equality is the
        whole point of the lead-z0v2 fix.
        """
        m = re.search(r'ca="([^"]+)"', script_body)
        if m:
            return m.group(1)
        return AGENT_VAULT_CONTAINER_CA_PATH

    def _write_container_ca_file(
        self, container_name: str, path: str, content: str | None = None
    ) -> None:
        """Record that a real, non-empty CA file was written at ``path``.

        ``content`` defaults to a fake PEM whose first line is the BEGIN
        CERTIFICATE marker (modelling `agent-vault ca fetch`).  An inline PEM
        is normalized to end with a newline, matching the script's
        ``printf '%s\\n'``.
        """
        if content is None:
            content = self._DEFAULT_FAKE_FETCHED_CA
        elif not content.endswith("\n"):
            content = content + "\n"
        self._container_files.setdefault(container_name, {})[path] = content

    def container_file(self, container_name: str, path: str) -> str | None:
        """The content of a file the launcher wrote inside the container, or
        None if no such file was written (lead-z0v2)."""
        return self._container_files.get(container_name, {}).get(path)

    def clone_git_ca_trust_path(self, container_name: str) -> str | None:
        """The CA path git was configured to trust on the clone exec — i.e. the
        GIT_SSL_CAINFO value on the `git clone` exec for this container, or None
        if the clone exec set none (lead-z0v2)."""
        for call in self.exec_calls:
            if (
                call.container == container_name
                and call.command[:2] == ["git", "clone"]
            ):
                return (call.env or {}).get("GIT_SSL_CAINFO")
        return None

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
        # lead-wdvx (Bug 2): a docker-dependent probe fails when the socket is
        # unreachable (daemon down OR a config fault: permission-denied /
        # not-mounted).  The decision to raise routes through the REAL
        # classifier (see `_maybe_raise_docker_unreachable`), so `status`
        # surfaces a cause-naming non-zero diagnostic instead of a (false)
        # "stopped" state ONLY when the classifier recognises the fault —
        # giving the Bug-2 status row genuine teeth.
        self._maybe_raise_docker_unreachable()
        return container_name in self._running

    def run(
        self,
        container_name: str,
        image: str,
        env: dict[str, str],
        mounts: list[tuple[str, str, str, bool]],
        network: str | None,
        detach: bool,
        group_add: list[str] | None = None,
    ) -> None:
        cmd = ["docker", "run", "--name", container_name]
        if detach:
            cmd.append("-d")
        self._container_env[container_name] = dict(env)
        self._container_mounts_full[container_name] = list(mounts)
        # lead-wdvx: record the supplementary groups the launcher granted
        # (`docker run --group-add <gid>`).  This is what `docker inspect`'s
        # HostConfig.GroupAdd would show — the container's supplementary groups.
        self._container_group_add[container_name] = [
            str(g) for g in (group_add or [])
        ]
        for key, val in env.items():
            cmd += ["-e", f"{key}={val}"]
            if key == "SHOPMSG_DSN":
                self._container_dsn[container_name] = val
            if key == "HTTPS_PROXY":
                self._container_proxy_env[container_name] = val
                self._container_broker[container_name] = val
            # lead-uiwu FACET 3: the broker CA arrives as inline PEM via
            # AGENT_VAULT_CA_PEM (ADR-045).  Record its presence so the
            # CA-materializer exec can model materializing from it (and so a
            # missing PEM leaves the clone untrusted).
            if key == "AGENT_VAULT_CA_PEM" and val:
                self._has_ca_pem.add(container_name)
                # lead-z0v2 — record the actual inline PEM value so the
                # clone-prep simulation can write its real content to disk.
                self._av_ca_pem_value[container_name] = val
        for gid in group_add or []:
            cmd += ["--group-add", str(gid)]
        for mount_type, source, dest, readonly in mounts:
            spec = f"type={mount_type},source={source},target={dest}"
            if readonly:
                spec += ",readonly"
            cmd += ["--mount", spec]
        if network:
            cmd += ["--network", network]
        cmd.append(image)
        self._assert_within_arg_strlen(cmd + ["sleep", "infinity"])
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

    def simulate_marker_delayed_past_seconds(
        self,
        container_name: str,
        tmux_session: str,
        marker: str,
        appears_after_seconds: float,
    ) -> None:
        """Configure a (container, session, marker) tuple as a SLOW brokered boot.

        lead-j351: the marker only becomes observable after
        ``appears_after_seconds`` of a *progressing* boot.  Combined with
        ``wait_for_pane_marker`` below — which simulates a pane that keeps
        changing every poll — this distinguishes a marker-keyed
        (progress-based) wait, which keeps polling and observes the marker,
        from a fixed-60s-deadline wait, which would abandon before
        ``appears_after_seconds`` when that exceeds 60s.
        """
        self._marker_delayed_after[
            (container_name, tmux_session, marker)
        ] = appears_after_seconds

    def marker_observed_after_legacy_deadline(
        self, container_name: str, legacy_deadline_seconds: float = 60.0
    ) -> bool:
        """True if any marker for this container was observed AFTER the legacy

        60s deadline — proving the wait keyed on the marker (progress) rather
        than abandoning at the fixed deadline that would have fired first.
        """
        return any(
            c == container_name and observed_at > legacy_deadline_seconds
            for (c, _s, _m), observed_at in self._marker_observed_at.items()
        )

    def input_ready_wait_preceded_prompt(self, prompt_substring: str) -> bool:
        """True if an input-ready-marker wait happened BEFORE the send-keys

        carrying ``prompt_substring`` — the inject-after-ready ordering
        (5ef728039884a9a2) preserved even for a slow boot.
        """
        for (container, sub), prompt_op in self._prompt_sendkeys_op.items():
            if sub != prompt_substring:
                continue
            wait_op = self._last_input_ready_wait_op.get(container)
            if wait_op is None or wait_op >= prompt_op:
                return False
            return True
        return False

    def capture_pane(
        self, container_name: str, tmux_session: str
    ) -> str:
        """One-shot pane capture (lead-q3uy).

        Records the call (as a capture-pane exec) so tests can assert the
        engage path read the pane, and returns the blocking option screen's
        rendered content when one is present and not yet dismissed; otherwise
        falls back to whatever pane content was configured.
        """
        self._op_seq += 1
        self.exec_calls.append(
            ExecCall(
                container=container_name,
                command=["tmux", "capture-pane", "-p", "-t", tmux_session],
                user="vscode",
                env=None,
            )
        )
        # lead-cw7m — a blocking readiness-wait prompt (BEFORE input-ready)
        # takes precedence: while present-and-undismissed, capture_pane returns
        # its rendered content so the controller's readiness-wait scan-dismiss
        # loop can detect, classify, and NAME it.  Once dismissed (Escape sent
        # and clears_on_escape), this falls through to the engage-phase
        # option-screen logic below, preserving lead-q3uy/gs03 behavior.
        rp = self._readiness_prompt.get(container_name)
        if rp is not None and not rp.get("dismissed"):
            return rp["content"]
        screen = self._option_screen.get(container_name)
        if screen and not screen.get("dismissed"):
            # lead-gs03 — the controller detects the blocking screen at this
            # capture_pane (engage Step 4b), AFTER the legitimate pre-prompt
            # engage keystrokes (claude-launch Enter, workspace-trust Enter).
            # Mark the screen DETECTED so the absorbed-keystroke recorder scopes
            # to keystrokes issued "between detecting the un-escapable option
            # screen and returning from launch" — not the earlier engage keys.
            screen["detected"] = True
            return screen["content"]
        # lead-gw9v — self-advance readiness modes drive the pane the launcher
        # captures during the initial readiness wait.
        mode = self._self_advance_mode.get(container_name)
        if mode is not None:
            from bc_launcher.controller import CLAUDE_INPUT_READY_MARKER
            if mode == "self_advance":
                # The agent self-advanced past trust: the pane is ALREADY at
                # the input-ready marker (no transient banner), so the launcher
                # can detect input-ready from the capture and skip the
                # trust-accept Enter.
                self._input_ready_observed.add(container_name)
                return (
                    "claude is ready\n"
                    f"{CLAUDE_INPUT_READY_MARKER}\n"
                )
            if mode == "pre_trust":
                # Before the trust-accept Enter, the pane shows the transient
                # trust banner (NOT the input-ready marker).  After trust is
                # accepted, the input-ready marker is observable via the marker
                # wait; the capture here is not relied upon on that path.
                if self._trust_accept_enter_count.get(container_name, 0) >= 1:
                    return f"{CLAUDE_INPUT_READY_MARKER}\n"
                return "Accessing workspace:\nQuick safety check\n"
            if mode == "neither":
                # Wedged: neither the banner nor the input-ready marker is
                # ever present.
                return "agent is still booting; no input prompt yet\n"
        return self._tmux_pane.get(container_name, "")

    def wait_for_pane_marker(
        self,
        container_name: str,
        tmux_session: str,
        marker: str,
        timeout_seconds: float,
        poll_interval_seconds: float = 0.5,
        _clock=None,
        _capture=None,
    ) -> bool:
        """Deterministic marker simulation.

        Success unless registered to time out.  lead-j351: when the marker is
        registered as a SLOW brokered boot via
        ``simulate_marker_delayed_past_seconds``, the wait is modelled as a
        progressing pane — the marker is observed only after the configured
        delay, but the boot keeps making progress, so a marker-keyed
        (progress-based) implementation observes it while a fixed-60s-deadline
        one would have abandoned.  The observed-at elapsed time is recorded so
        tests can assert the marker landed after the legacy 60s deadline.
        """
        self._op_seq += 1
        self.wait_for_marker_calls.append((container_name, tmux_session, marker))

        key = (container_name, tmux_session, marker)
        from bc_launcher.controller import (
            CLAUDE_INPUT_READY_MARKER,
            CLAUDE_READY_MARKER,
        )
        if marker == CLAUDE_INPUT_READY_MARKER:
            self._last_input_ready_wait_op[container_name] = self._op_seq
            # lead-cw7m — a blocking readiness-wait prompt prevents the agent
            # from reaching the input-ready marker.  While the prompt is
            # present-and-undismissed, the input-ready wait does NOT observe
            # the marker (returns False); the controller's scan-dismiss loop
            # must then capture the pane, Esc-dismiss the prompt, and re-wait.
            rp = self._readiness_prompt.get(container_name)
            if rp is not None and not rp.get("dismissed"):
                return False

        # lead-gw9v — self-advance readiness modes.  Resolve the workspace-trust
        # gate per the configured mode (see ``_self_advance_mode`` doc).
        mode = self._self_advance_mode.get(container_name)
        if mode is not None:
            if marker == CLAUDE_READY_MARKER:
                # The transient PRE-trust banner is observed FIRST only in the
                # pre-trust mode; in self-advance and neither modes it is never
                # caught by polling.
                return mode == "pre_trust"
            if marker == CLAUDE_INPUT_READY_MARKER:
                if mode == "self_advance":
                    # Already at input-ready; the marker is observable now.
                    self._input_ready_observed.add(container_name)
                    return True
                if mode == "neither":
                    # Never reaches input-ready within the readiness timeout.
                    return False
                if mode == "pre_trust":
                    # Input-ready becomes observable only AFTER the trust-accept
                    # Enter has been sent.
                    observed = self._trust_accept_enter_count.get(
                        container_name, 0
                    ) >= 1
                    if observed:
                        self._input_ready_observed.add(container_name)
                    return observed

        if key in self._marker_timeouts:
            return False

        delay = self._marker_delayed_after.get(key)
        if delay is not None:
            # The pane keeps PROGRESSING (changes every poll), so the wait is
            # not abandoned at a fixed deadline; it keeps polling until the
            # marker appears at `delay` simulated seconds.  A faithful
            # progress-based wait observes it; record the observed-at time.
            #
            # Guard: only honour the delayed-marker (i.e. treat it as
            # observed) when the wait is marker-keyed — modelled here by the
            # caller passing a timeout that is a no-progress/idle budget.  The
            # production controller passes CLAUDE_READINESS_TIMEOUT_SECONDS as
            # exactly that idle budget, so the marker IS observed.
            self._marker_observed_at[key] = delay
            return True

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
        if self._all_brokers_unreachable:
            return False
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
        detach: bool = False,
        input: str | None = None,
    ) -> subprocess.CompletedProcess:
        # lead-m4zt: model the kernel's MAX_ARG_STRLEN per-argument limit FIRST
        # — before recording the call or mutating any state — so an over-long
        # single argv element fails the spawn with E2BIG exactly as the real
        # docker exec does (the container is left without the intended effect).
        # A blob on STDIN (``input``) is not an argv element, so streaming it
        # keeps every argv element small and never trips the limit.
        self._assert_within_arg_strlen(command)
        self._op_seq += 1
        self.exec_calls.append(
            ExecCall(
                container=container_name,
                command=command,
                user=user,
                env=dict(env) if env else None,
                detach=detach,
                input=input,
            )
        )
        # lead-j351: record the op index of any send-keys carrying a non-empty
        # text token, keyed by (container, token), so a test can assert the
        # input-ready wait preceded the prompt injection (inject-after-ready).
        if command[:2] == ["tmux", "send-keys"]:
            # Skip option flags, the "-t <session>" target pair, and bare
            # "Enter"; record the op index of each remaining text token.
            skip_next = False
            for tok in command[2:]:
                if skip_next:
                    skip_next = False
                    continue
                if tok == "-t":
                    skip_next = True
                    continue
                if tok == "Enter" or tok.startswith("-"):
                    continue
                self._prompt_sendkeys_op.setdefault(
                    (container_name, tok), self._op_seq
                )
                # lead-pixf (aeebb281): count `agent-vault run -- claude ...`
                # launch keystrokes so the start-agent no-op scenario can
                # assert NO second claude was started against an already-live
                # agent.
                if "agent-vault run -- claude" in tok:
                    self._claude_launch_count[container_name] = (
                        self._claude_launch_count.get(container_name, 0) + 1
                    )
        prefix = ["docker", "exec"]
        if user is not None:
            prefix += ["-u", user]
        self._last_command = prefix + [container_name] + command

        # lead-uiwu FACET 2 — capture the /workspace owner as it stood BEFORE
        # this exec's own write re-owns it, so the clone simulation can decide
        # Permission-denied against the ownership the clone actually encountered
        # (set by a prior pre-clone chown), not the ownership the clone's own
        # write leaves behind.
        _ws_owner_before = self._workspace_path_owner.get(
            container_name, {}
        ).get(CONTAINER_WORKSPACE, "root")

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

        # lead-m4zt: `tmux load-buffer -` reads the off-argv prompt from the
        # exec's STDIN into a named tmux buffer.  The blob rides ``input``
        # (docker exec -i), never the argv, so it is immune to MAX_ARG_STRLEN.
        if command[:2] == ["tmux", "load-buffer"]:
            self._tmux_loaded_buffer[container_name] = input or ""
            return subprocess.CompletedProcess(command, 0, "", "")

        # lead-m4zt: `tmux paste-buffer` deposits the loaded buffer into the
        # agent's input as a SINGLE paste write (the same paste shape a text
        # send-keys produces) — unsubmitted, exactly like the text-token half of
        # the two-discrete-writes submit.  The discrete Enter send-keys that
        # follows commits it via the existing bare-Enter model below.
        if command[:2] == ["tmux", "paste-buffer"]:
            state = self._agent_state.setdefault(
                container_name, {"buffer": None, "processing": None}
            )
            pasted = self._tmux_loaded_buffer.get(container_name, "")
            if pasted:
                state["buffer"] = pasted
            return subprocess.CompletedProcess(command, 0, "", "")

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

            # lead-cw7m — a present, not-yet-dismissed blocking readiness-wait
            # prompt (BEFORE input-ready) intercepts keystrokes.  A discrete
            # Escape DISMISSES it (clears_on_escape variant), after which the
            # input-ready marker becomes observable.  The never-clears variant
            # records the Escape but keeps re-presenting the prompt, so the
            # input-ready marker is never observed and the controller must stop
            # at the 60s bound.  NEVER does an Enter/'1' dismiss this prompt —
            # only Escape — so a renderer-enabling keystroke can never clear it.
            rp = self._readiness_prompt.get(container_name)
            if rp is not None and not rp.get("dismissed"):
                if payload == ["Escape"]:
                    rp["escape_count"] += 1
                    if rp.get("clears_on_escape"):
                        rp["dismissed"] = True
                # Whether cleared or not, the prompt consumed this keystream;
                # nothing lands in the agent input buffer.
                return subprocess.CompletedProcess(command, 0, "", "")

            # lead-q3uy — a present, not-yet-dismissed blocking option screen
            # intercepts keystrokes.  A discrete Escape send-keys DISMISSES an
            # escapable screen (after which the input prompt is reachable
            # again); any other keystream is absorbed by the screen and does
            # NOT reach the agent input buffer.  A non-escapable screen is not
            # dismissed by Escape.
            screen = self._option_screen.get(container_name)
            if screen and not screen.get("dismissed"):
                # lead-gs03 — RECORD every keystream the present screen absorbs
                # BEFORE acting on it, so a phantom Enter (or any keystroke)
                # against the screen is visible to the send-keys recorder rather
                # than silently swallowed.  Scope the record to keystrokes
                # issued AFTER the controller detected the screen (its Step 4b
                # capture_pane set screen["detected"]); pre-detection engage keys
                # (claude-launch Enter, workspace-trust Enter) are legitimate and
                # are NOT "between detecting the un-escapable screen and
                # returning from launch".  The Escape that DISMISSES an escapable
                # screen is recorded here (it arrives post-detection); the
                # un-escapable scenario asserts no such absorbed keystroke is an
                # Enter / any key.
                if screen.get("detected"):
                    self._keystrokes_while_screen_present.setdefault(
                        container_name, []
                    ).append(list(payload))
                if payload == ["Escape"] and screen.get("escapable"):
                    screen["dismissed"] = True
                # Whether dismissed or not, the screen consumed this keystream;
                # nothing lands in the agent input buffer.
                return subprocess.CompletedProcess(command, 0, "", "")

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
                    # lead-gw9v — a BARE Enter send-keys issued BEFORE the
                    # input-ready marker has been observed, while a self-advance
                    # mode is configured, is the trust-accept Enter.  Count it
                    # so the scenarios can assert the launcher SKIPPED it on the
                    # self-advance path (input-ready observed first, no trust
                    # Enter) and SENT it on the pre-trust path.  The
                    # claude-launch keystream is a text+Enter call (not a bare
                    # Enter); the prompt-submit Enter arrives only AFTER
                    # input-ready is observed.  This counting is independent of
                    # the buffer-commit model (Step 1's text+Enter paste leaves
                    # the claude command buffered, which the trust Enter would
                    # otherwise "commit" — so the buffer state is NOT a reliable
                    # discriminator here).
                    if (
                        container_name in self._self_advance_mode
                        and container_name not in self._input_ready_observed
                    ):
                        self._trust_accept_enter_count[container_name] = (
                            self._trust_accept_enter_count.get(
                                container_name, 0
                            ) + 1
                        )
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

        # Simulate the legacy agent-vault broker CA materializer entrypoint
        # script (bclaunch-9rr).  Running it materializes the broker MITM root
        # CA into the container trust store — but ONLY when the inline
        # AGENT_VAULT_CA_PEM env was supplied (modelling the real entrypoint
        # guard `if [ -n "$AGENT_VAULT_CA_PEM" ]`).  Retained for any caller
        # that still invokes the bare entrypoint script.
        if command[:1] == ["/usr/local/bin/agent-vault-ca.sh"] or (
            command and command[0].endswith("agent-vault-ca.sh")
        ):
            if container_name in self._has_ca_pem:
                self._broker_ca_materialized.add(container_name)
                self._write_container_ca_file(
                    container_name, AGENT_VAULT_CONTAINER_CA_PATH
                )
            return subprocess.CompletedProcess(command, 0, "", "")

        # lead-z0v2 — simulate the clone-prep CA materialization script the
        # controller now runs (`/bin/sh -c "<script>"`).  This is the REAL-
        # FIDELITY model that catches the v0.3.34 regression: the script must
        # actually WRITE real CA *content* to a path and the launcher must point
        # git at that SAME path.  We faithfully replicate the script's logic:
        #   * extract the CA target path the script writes to;
        #   * source the content from AGENT_VAULT_CA_PEM (inline PEM) when set,
        #     ELSE from `agent-vault ca fetch` when the broker CA is fetchable;
        #   * the file is non-empty (BEGIN CERTIFICATE) only when one source
        #     produced content; otherwise the script exits NON-ZERO (the prep
        #     fails and the launcher must refuse to point git at the path).
        # A test that supplies NEITHER an inline PEM NOR a fetchable broker CA
        # thus reproduces the real bug: nothing is written, prep fails, and
        # git would have been pointed at a path that does not exist.
        if (
            command[:2] == ["/bin/sh", "-c"]
            and len(command) >= 3
            and "AGENT_VAULT_CA_PEM" in command[2]
            and "ca fetch" in command[2]
        ):
            script_body = command[2]
            ca_target = self._extract_ca_path_from_prep(script_body)
            pem = self._av_ca_pem_value.get(container_name)
            wrote = False
            if pem:
                # (1) inline PEM (ADR-045).
                self._write_container_ca_file(container_name, ca_target, pem)
                wrote = True
            elif container_name in self._broker_ca_fetchable:
                # (2) operator path: `agent-vault ca fetch`.
                self._write_container_ca_file(container_name, ca_target)
                wrote = True
            if not wrote:
                # No source produced CA content: the script's verify step
                # (`[ -s "$ca" ]`) fails and it exits non-zero.  This is the
                # exact regression — the launcher would otherwise have pointed
                # git at an unwritten path.
                return subprocess.CompletedProcess(
                    command, 1, "",
                    f"agent-vault CA file is empty: {ca_target}\n",
                )
            self._broker_ca_materialized.add(container_name)
            return subprocess.CompletedProcess(command, 0, "", "")

        # Simulate git clone (lead-uiwu FACETs 2 + 3).
        if command[0] == "git" and command[1] == "clone":
            # FACET 3 ordering teeth: record whether the broker CA was
            # materialized BEFORE this (first) clone attempt, independent of the
            # clone's own outcome.
            self._ca_materialized_before_clone.setdefault(
                container_name,
                container_name in self._broker_ca_materialized,
            )

            # FACET 2 (scn 4154b0ea63d0516b): a clone performed AS the agent
            # user (vscode) into a ROOT-owned /workspace fails
            # "/workspace/.git: Permission denied".  The clone succeeds only
            # when /workspace is vscode-owned (the launcher chowns it before the
            # clone), OR when the clone runs as root (the legacy path).
            ws_owner = _ws_owner_before
            if user == AGENT_CONTAINER_USER and ws_owner != AGENT_CONTAINER_USER:
                return subprocess.CompletedProcess(
                    command, 1, "",
                    f"fatal: could not create work tree dir "
                    f"'{CONTAINER_WORKSPACE}': could not create leading "
                    f"directories of '{CONTAINER_WORKSPACE}/.git': "
                    f"{CONTAINER_WORKSPACE}/.git: Permission denied\n",
                )

            # FACET 3 (scn 09f871cf8b99a34b, lead-z0v2): a clone routed through
            # the agent-vault MITM proxy (clone exec carries an HTTPS_PROXY)
            # FAILS TLS verification UNLESS git is configured to trust a CA path
            # that NAMES A REAL, NON-EMPTY CA FILE with a BEGIN CERTIFICATE
            # first line.  This is the write-path==trust-path invariant with
            # teeth: the actual bug (git pointed at GIT_SSL_CAINFO=<path> while
            # nothing was written to <path>) produces the EXACT real-container
            # failure "error setting certificate file: <path>".  An unproxied
            # clone (no HTTPS_PROXY on the exec env) is unaffected.
            clone_proxied = bool(env and (env.get("HTTPS_PROXY") or env.get("https_proxy")))
            if clone_proxied:
                ca_trust_path = (env or {}).get("GIT_SSL_CAINFO")
                files = self._container_files.get(container_name, {})
                if ca_trust_path is not None:
                    # git is explicitly pointed at a CA path: that path MUST
                    # name a real, non-empty BEGIN-CERTIFICATE file, else git
                    # cannot open it ("error setting certificate file").
                    content = files.get(ca_trust_path)
                    if not content:
                        return subprocess.CompletedProcess(
                            command, 1, "",
                            f"fatal: unable to access '...': error setting "
                            f"certificate file: {ca_trust_path}\n",
                        )
                    if not content.startswith("-----BEGIN CERTIFICATE-----"):
                        return subprocess.CompletedProcess(
                            command, 1, "",
                            f"fatal: unable to access '...': error setting "
                            f"certificate file: {ca_trust_path} (not a PEM)\n",
                        )
                elif container_name not in self._broker_ca_materialized:
                    # git relies on the system/default trust store: the broker
                    # CA must have been installed there before the clone.
                    return subprocess.CompletedProcess(
                        command, 1, "",
                        "fatal: unable to access '...': SSL certificate problem: "
                        "unable to get local issuer certificate\n",
                    )

            # Clone succeeds: /workspace is now a git repo cloned from the
            # remote (command[2] is the remote URL, command[3] the dest).
            if len(command) >= 3:
                self._workspace_cloned_from[container_name] = command[2]
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
        # lead-5k8c — the launcher's empty-remote SEED step.  Recognised by its
        # `git ls-remote ... refs/dolt` verification tail inside a login-shell
        # script.  When the remote is empty (and the seed is not forced to
        # fail) the seed succeeds and marks the remote seeded, after which
        # `bd bootstrap` succeeds; a forced-fail seed exits non-zero.
        # lead-7jc2 — the launcher's ABSENT-repo CREATE step.  When the
        # `<bc>-beads` GitHub tracker repo does not exist ("Repository not
        # found"), the launcher creates it with an initial branch/commit
        # (`gh repo create ... --add-readme`).  After creation the repo EXISTS
        # but its Dolt remote is still EMPTY/uninitialized (no refs/dolt yet),
        # so a subsequent `bd bootstrap` fails "git remote has no branches"
        # until the empty-remote seed step initializes it — exactly the
        # existing lead-5k8c seed path.
        # lead-r34c / GAP B — the launcher's ORIGIN_OWNER writeback step.  It
        # RESOLVES the derived owner from the container's /workspace git origin
        # and WRITES it into the .beads config sync.remote + the functional bd
        # dolt remote BEFORE bd bootstrap, so no literal ORIGIN_OWNER survives
        # and the clone target becomes <owner>/<bc>-beads.  The modelled effect:
        # the functional remote's owner segment becomes the container's derived
        # origin owner and the ORIGIN_OWNER placeholder is cleared.
        if _is_origin_owner_writeback_command(command):
            owner = self._container_origin_owner.get(container_name, "")
            if owner:
                self._beads_functional_remote_owner[container_name] = owner
                self._beads_remote_owner_placeholder.discard(container_name)
                return subprocess.CompletedProcess(
                    command, 0,
                    f"resolved ORIGIN_OWNER -> {owner} in .beads config and "
                    "functional bd dolt remote\n",
                    "",
                )
            # No derivable origin owner (unconfigured) — the writeback cannot
            # resolve; the placeholder survives and bootstrap will still fail.
            return subprocess.CompletedProcess(
                command, 1, "",
                "could not resolve owner from /workspace git origin\n",
            )

        if _is_repo_create_command(command):
            self._beads_repo_created.add(container_name)
            self._beads_repo_absent.discard(container_name)
            self._beads_remote_empty.add(container_name)
            return subprocess.CompletedProcess(
                command, 0,
                "Created repository with an initial branch and commit "
                "(refs/heads/main present)\n",
                "",
            )

        if _is_empty_remote_seed_command(command):
            if container_name in self._beads_remote_seed_fails:
                return subprocess.CompletedProcess(
                    command, 1, "",
                    "fatal: could not initialize empty beads dolt remote\n",
                )
            self._beads_remote_seeded.add(container_name)
            self._beads_remote_empty.discard(container_name)
            return subprocess.CompletedProcess(
                command, 0,
                "Initialized empty beads dolt remote; pushed initial "
                "branch/commit (refs/dolt/data present)\n",
                "",
            )

        if is_bd_bootstrap_command(command):
            # lead-r34c / GAP B — while the functional bd dolt remote still
            # carries the scaffolded ORIGIN_OWNER placeholder (the standup did
            # NOT resolve it to the derived owner before bootstrap), the clone
            # target is ORIGIN_OWNER/<bc>-beads and fails "Repository not found"
            # — the exact David-2026-07-07 standup failure GAP B closes.
            if container_name in self._beads_remote_owner_placeholder:
                return subprocess.CompletedProcess(
                    command, 1, "",
                    "dolt clone git+https://github.com/ORIGIN_OWNER/"
                    f"{container_name}-beads.git: Repository not found; the "
                    "remote repository does not exist\n",
                )
            # lead-7jc2 — an ABSENT `<bc>-beads` GitHub tracker repo makes the
            # bootstrap clone fail "Repository not found" (a strictly earlier
            # failure than an empty-but-existing remote) UNTIL the launcher's
            # absent-repo provisioning step CREATES it.
            if (
                container_name in self._beads_repo_absent
                and container_name not in self._beads_repo_created
            ):
                return subprocess.CompletedProcess(
                    command, 1, "",
                    "dolt clone git+https://github.com/dstengle/"
                    f"{container_name}-beads.git: Repository not found; the "
                    "remote repository does not exist\n",
                )
            # lead-5k8c — an EMPTY/uninitialized `<bc>-beads` Dolt remote makes
            # the bootstrap clone fail "git remote has no branches" UNTIL the
            # launcher's empty-remote seed step initializes it.
            if (
                container_name in self._beads_remote_empty
                and container_name not in self._beads_remote_seeded
            ):
                # lead-ypnz / GAP D — emit the per-container OVERRIDE error text
                # when one is configured (e.g. the CURRENT bc-base dolt "clone
                # failed; remote at that url contains no Dolt data"), else the
                # legacy "git remote has no branches" text.  The controller's
                # empty-remote-seed classifier must recognise BOTH.
                override = self._beads_remote_empty_error.get(container_name)
                if override is not None:
                    return subprocess.CompletedProcess(command, 1, "", override + "\n")
                return subprocess.CompletedProcess(
                    command, 1, "",
                    "dolt clone git+https://github.com/dstengle/"
                    f"{container_name}-beads.git: git remote has no branches: "
                    "cannot push to a remote with no branches; initialize the "
                    "repository with an initial branch/commit first\n",
                )
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

        # Simulate `bd dolt remote list` — lead-r34c / GAP B.  Reports the
        # functional bd dolt remote (the one bd bootstrap clones from), keyed
        # off the current owner segment.  Before the launcher's writeback step
        # the owner segment is the scaffolded ORIGIN_OWNER placeholder; after it
        # is the derived GitHub owner.  Recognised in bare and login-shell form.
        if command[:4] == ["bd", "dolt", "remote", "list"] or (
            len(command) >= 3
            and command[0] == "bash"
            and command[1] in ("-lc", "-c")
            and "bd dolt remote list" in command[2]
        ):
            url = self.beads_functional_remote_url(container_name)
            listing = f"origin {url}\n" if url else ""
            return subprocess.CompletedProcess(command, 0, listing, "")

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
                # lead-ona9 (7700eea079ffe1d8): the SAME pour that emits
                # ".claude/skills/" also emits "/workspace/.fabro/" — the fabro
                # loop def is delivered by the pour exactly as the skill-group
                # is, no longer streamed from a baked asset.
                self._workspace_fabro[container_name] = True
            return subprocess.CompletedProcess(command, 0, "", "")

        # lead-a3kg — read the poured "/workspace/.fabro/workflow.toml" IN the
        # container (a `base64 <path>` exec that base64-encodes the poured file
        # to stdout).  The N4 fabro wiring reads THIS file — not the retired
        # baked host asset — so its BC_NAME/WORK_ID rewrite operates on the
        # poured/committed def actually present in the container.  Returns the
        # seeded poured content (or, unseeded, the canonical def-source mirror
        # bytes the pour delivers), base64-encoded on stdout exactly as the real
        # `base64` coreutil would.
        if (
            command[:2] == ["/bin/sh", "-c"]
            and len(command) >= 3
            and FABRO_WORKFLOW_TOML_CONTAINER_PATH in command[2]
            and "base64" in command[2]
            and "base64 -d" not in command[2]
        ):
            content = self._poured_workflow_toml.get(container_name)
            if content is None:
                with open(_ASSET_WORKFLOW_TOML, encoding="utf-8") as fh:
                    content = fh.read()
            import base64 as _b64
            encoded = _b64.b64encode(content.encode("utf-8")).decode("ascii")
            return subprocess.CompletedProcess(command, 0, encoded, "")

        # lead-e5jx — read the poured "/workspace/.fabro/dispatcher.toml" IN the
        # container (a `base64 <path>` exec).  The reactive engage runs `fabro
        # run dispatcher.toml`; its native watch/dispatch nodes read $BC_NAME
        # from dispatcher.toml's [run.environment.env] overlay, so the launcher
        # must rewrite THIS file's BC_NAME/WORK_ID to the launch identity too.
        # Returns the seeded poured content (or, unseeded, the canonical
        # def-source mirror bytes the pour delivers), base64-encoded on stdout.
        if (
            command[:2] == ["/bin/sh", "-c"]
            and len(command) >= 3
            and FABRO_DISPATCHER_TOML_CONTAINER_PATH in command[2]
            and "base64" in command[2]
            and "base64 -d" not in command[2]
        ):
            content = self._poured_dispatcher_toml.get(container_name)
            if content is None:
                with open(_ASSET_DISPATCHER_TOML, encoding="utf-8") as fh:
                    content = fh.read()
            import base64 as _b64
            encoded = _b64.b64encode(content.encode("utf-8")).decode("ascii")
            return subprocess.CompletedProcess(command, 0, encoded, "")

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

        # lead-ckq5 — `fabro --version` inside the running container. fabro is a
        # baked binary that cannot run in this environment, so the model reports
        # the seeded fabro version (default v0.254.0, derived from the Dockerfile
        # pin at seed time) and exit 0. When fabro is modelled absent the exec
        # exits non-zero (the a3512aedb8763150 fabro-leg teeth).
        if command[:2] == ["fabro", "--version"] or (
            len(command) >= 3
            and command[0] in ("bash", "sh")
            and command[1] in ("-lc", "-c")
            and command[2].strip() == "fabro --version"
        ):
            fabro_absent = "fabro" in self._container_tool_absent.get(
                container_name, set()
            )
            version = self._container_fabro_version.get(container_name)
            if version is None or fabro_absent:
                return subprocess.CompletedProcess(
                    command, 127, "", "fabro: command not found\n"
                )
            return subprocess.CompletedProcess(
                command, 0, f"fabro version {version}\n", ""
            )

        # lead-h755 — `command -v <tool>` resolves against the per-container
        # in-container PATH model.  Recognise both the bare vector
        # ["command", "-v", "<tool>"] and a shell-wrapped form
        # ["bash"/"sh", "-lc"/"-c", "command -v <tool>"].  When the tool is
        # resolvable: exit 0 + the absolute path on stdout (what `command -v`
        # prints).  When NOT resolvable: exit 1 + empty output.  This is what
        # gives the regression guard its teeth — an absent gh/agent-vault
        # exits non-zero and prints no path.
        tool_query = None
        if command[:2] == ["command", "-v"] and len(command) >= 3:
            tool_query = command[2]
        elif (
            len(command) >= 3
            and command[0] in ("bash", "sh")
            and command[1] in ("-lc", "-c")
            and command[2].strip().startswith("command -v ")
        ):
            tool_query = command[2].strip().split("command -v ", 1)[1].strip()
        if tool_query is not None:
            absent = tool_query in self._container_tool_absent.get(
                container_name, set()
            )
            path = self._container_tool_path.get(
                container_name, {}
            ).get(tool_query)
            if path and not absent:
                return subprocess.CompletedProcess(
                    command, 0, path + "\n", ""
                )
            return subprocess.CompletedProcess(command, 1, "", "")

        # Default: success
        return subprocess.CompletedProcess(command, 0, "", "")

    def workspace_skills(self, container_name: str) -> set[str]:
        """Return the skill-group entries present in the workspace .claude/skills/."""
        return set(self._workspace_skills.get(container_name, set()))

    def set_poured_workflow_toml(
        self, container_name: str, content: str
    ) -> None:
        """lead-a3kg — seed the CONTENT of the poured/committed
        "/workspace/.fabro/workflow.toml" as it stands inside ``container_name``.

        The N4 fabro wiring reads this file in-container (a `base64 <path>`
        exec) and rewrites its BC_NAME/WORK_ID.  Seeding distinctive content
        (e.g. the bundle-default identity plus a unique sentinel) lets a test
        prove the rewrite was derived from the CONTAINER file rather than the
        retired baked host asset."""
        self._poured_workflow_toml[container_name] = content

    def set_poured_dispatcher_toml(
        self, container_name: str, content: str
    ) -> None:
        """lead-e5jx — seed the CONTENT of the poured/committed
        "/workspace/.fabro/dispatcher.toml" as it stands inside
        ``container_name``.

        The reactive engage (`fabro run dispatcher.toml`) reads $BC_NAME from
        this file's [run.environment.env] overlay, so the launcher rewrites its
        BC_NAME/WORK_ID to the launch identity in-container (a `base64 <path>`
        read + `base64 -d` write-back).  Seeding distinctive content (e.g. the
        bundle-default identity plus a unique sentinel) lets a test prove the
        rewrite was derived from the CONTAINER file and targets the launch BC
        rather than the bundle default `fabro-throwaway`."""
        self._poured_dispatcher_toml[container_name] = content

    def workspace_fabro(self, container_name: str) -> bool:
        """True if the shop-templates pour has emitted "/workspace/.fabro/" into
        the workspace (lead-ona9, 7700eea079ffe1d8) — the fabro loop def
        delivered by the SAME pour that emits ".claude/skills/"."""
        return bool(self._workspace_fabro.get(container_name, False))

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
        # lead-pixf (010e776c) / lead-wdvx (Bug 2): a docker-dependent call
        # fails with a recognizable stderr when the socket is unreachable
        # (daemon-down OR a config fault: permission-denied / not-mounted).
        # The DECISION to treat that as an infra failure (raise) vs. an
        # ordinary empty result is made by the REAL classifier
        # `_is_docker_socket_unreachable` — so the fake routes the canned
        # stderr through it, giving the Bug-2 scenarios genuine teeth: a
        # classifier that does NOT match the permission-denied / not-mounted
        # stderr makes this fall through to the empty list, masking the fault
        # exactly as the unfixed code does.
        self._maybe_raise_docker_unreachable()
        return [
            ContainerInfo(name=name, running=running)
            for name, running in self._all_containers.items()
        ]

    def _maybe_raise_docker_unreachable(self) -> None:
        """Route the canned docker fault stderr through the REAL classifier.

        Mirrors the real driver: a docker CLI call returns non-zero with the
        fault stderr; the driver raises ``DockerSocketUnreachableError`` ONLY
        when ``_is_docker_socket_unreachable`` classifies that stderr as a
        socket-unreachable failure.  When no fault is configured, or the
        classifier does not match the configured stderr, no raise — the caller
        proceeds to its ordinary (empty/absent) result.
        """
        stderr = self._docker_socket_unreachable
        if stderr is None:
            return
        from bc_launcher.driver import (
            DockerSocketUnreachableError,
            _is_docker_socket_unreachable,
        )
        if _is_docker_socket_unreachable(stderr):
            raise DockerSocketUnreachableError(stderr)

    def agent_online(self, container_name: str) -> bool:
        """lead-pixf (f2ddd6c7 / aeebb281): report agent presence.

        Online only when the container has been modelled (via
        ``set_agent_online``) as holding a live claude with an armed
        ``shop-msg watch`` AND its "agent" tmux session exists — mirroring
        the three-part determinant the real driver probes.
        """
        if container_name not in self._agent_online_containers:
            return False
        return "agent" in self._tmux_sessions.get(container_name, set())

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

    # --- Scenario af2f03d3ac519cb5: local cache / pull / served-digest -------

    def seed_local_cache(self, image_ref: str, digest: str) -> None:
        """Seed the in-memory local Docker cache so ``image_ref`` serves ``digest``.

        Used to model the stale state: ``repo:latest`` cached at ``D_old`` while
        the registry has since republished ``latest`` at ``D_new``.  A run that
        references the bare ``:latest`` tag therefore serves ``D_old`` from this
        cache; only a digest-pinned reference that was PULLED serves ``D_new``.
        """
        self._local_image_cache[image_ref] = digest

    def set_registry_for_pull(self, registry_driver) -> None:
        """Wire the registry the fake consults when ``pull`` fetches content.

        On ``pull(repo@sha256:Dnew)`` the fake records the digest content for
        that pinned reference so a subsequent run of the pin serves ``D_new``.
        On ``pull(repo:latest)`` the fake would re-resolve the tag against the
        registry and update the cached tag content (modelling ``docker pull``
        of a moving tag).
        """
        self._registry_for_pull = registry_driver

    def pull(self, image_ref: str) -> None:
        self.pull_calls.append(image_ref)
        self.operation_log.append(("pull", image_ref))
        # A digest-pinned reference (repo@sha256:...) names content directly:
        # pulling it makes that exact content available in the local cache.
        if "@sha256:" in image_ref:
            digest = image_ref.split("@", 1)[1]
            self._local_image_cache[image_ref] = digest
            return
        # A tag reference: pulling re-resolves the tag against the registry
        # (if wired) and updates what the cached tag serves — the corrective
        # action that a manual ``docker pull repo:latest`` performs.
        if self._registry_for_pull is not None:
            self._local_image_cache[image_ref] = (
                self._registry_for_pull.resolve_digest(image_ref)
            )

    def served_digest_for_run(self, container_name: str) -> str | None:
        """Return the DIGEST CONTENT the container's run image actually serves.

        Resolves the trailing image token of the recorded ``docker run`` command
        against the local cache: a digest-pinned reference serves its pinned
        content (only if that content was pulled into the cache); a bare tag
        serves whatever the local cache holds under that tag (the stale
        ``D_old`` when the registry has since moved the tag).  ``None`` when the
        reference is not present in the local cache (an unpulled digest pin —
        i.e. a run that would have to pull on demand, which the fake does not
        auto-populate).
        """
        run_cmd = self._run_commands_by_container.get(container_name, [])
        image_tokens = [t for t in run_cmd if "shopsystem-bc-base" in t]
        if not image_tokens:
            return None
        image_ref = image_tokens[0]
        return self._local_image_cache.get(image_ref)

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

    def agent_committed_input(self, container_name: str) -> str | None:
        """lead-m4zt: the prompt text the modelled agent has COMMITTED and is
        processing (its input was submitted), or None when the agent is idle at
        an unsubmitted buffer.  This is the "the BC is engaged / online" signal
        for the tmux engage path — the startup prompt reached the agent loop and
        was submitted, regardless of whether it arrived via a small
        send-keys text write or an off-argv load-buffer/paste-buffer stream.
        """
        state = self._agent_state.get(container_name)
        return state.get("processing") if state else None

    def send_keys_calls(self, container_name: str) -> list[ExecCall]:
        """Return all recorded tmux send-keys exec calls for the container."""
        return [
            c for c in self.exec_calls
            if c.container == container_name and c.command[:2] == ["tmux", "send-keys"]
        ]

    def keystrokes_absorbed_by_screen(self, container_name: str) -> list[list[str]]:
        """Return send-keys payloads absorbed while the option screen was present.

        lead-gs03 — each entry is a send-keys payload (the "-t <session>" target
        tokens stripped) that the present-and-undismissed blocking option screen
        consumed.  The tightened un-escapable scenario asserts this list carries
        ZERO Enter-bearing invocations and ZERO keystrokes of any kind, proving
        the launcher issued nothing against the un-escapable screen — closing
        the phantom-Enter gap the prior buffer-only assertion missed.
        """
        return list(self._keystrokes_while_screen_present.get(container_name, []))

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

    # --- lead-zxtk: workspace-mount host-tree model ------------------------
    # A workspace-mount launch bind-mounts an existing host working tree at
    # /workspace and must SKIP the clone AND all clone-path provisioning so the
    # mounted tree's `.beads` registry and `.claude/skills` stay byte-unchanged.
    # The model captures the host tree's `.beads`/`.claude/skills` content at
    # setup time; the CURRENT content of the mounted tree is that same snapshot
    # UNLESS a provisioning op (bd bootstrap, or `shop-templates update`) was
    # exec'd against the container — either of which would mutate the live tree.
    # So a launcher that fails to skip provisioning under workspace-mount reads
    # RED here (the byte-unchanged assertion fails), giving the scenario teeth.

    def set_host_tree_snapshot(
        self,
        host_path: str,
        beads_registry: str,
        claude_skills: str,
        fabro_def: str | None = None,
    ) -> None:
        """Record the host working tree's committed `.beads` registry blob,
        poured `.claude/skills` content, and (lead-ona9) committed
        `/workspace/.fabro/` def content prior to launch (lead-zxtk).

        ``fabro_def`` models a committed `.fabro/` tree exactly as a
        poured-then-committed BC repo carries it; on a workspace-mount launch it
        must be presented byte-unchanged (the pour is skipped)."""
        if not hasattr(self, "_host_tree_snapshot"):
            self._host_tree_snapshot: dict[str, dict[str, str]] = {}
        self._host_tree_snapshot[host_path] = {
            "beads": beads_registry,
            "skills": claude_skills,
            "fabro": fabro_def or "",
        }

    def bd_bootstrap_ran(self, container_name: str) -> bool:
        """True if a `bd bootstrap` provisioning step was exec'd against the
        container (lead-zxtk: must be False for a workspace-mount launch)."""
        return any(
            c.container == container_name and is_bd_bootstrap_command(c.command)
            for c in self.exec_calls
        )

    def shop_templates_update_ran(self, container_name: str) -> bool:
        """True if a `shop-templates update` re-pour was exec'd against the
        container (lead-zxtk: must be False for a workspace-mount launch)."""
        return any(
            c.container == container_name
            and c.command[:2] == ["shop-templates", "update"]
            for c in self.exec_calls
        )

    def mounted_tree_byte_unchanged(
        self, container_name: str, host_path: str
    ) -> bool:
        """True if the mounted host tree's `.beads`/`.claude/skills` are
        byte-unchanged after launch (lead-zxtk).

        The mounted tree is the bind-mount source recorded for the container.
        Its content is byte-unchanged exactly when NO provisioning op mutated
        it — i.e. neither `bd bootstrap` nor `shop-templates update` ran against
        the container.  (The snapshot blobs themselves are the host-supplied
        content; this model treats any provisioning exec as a mutation, which is
        what a clone-path launch would do.)
        """
        snapshot = getattr(self, "_host_tree_snapshot", {}).get(host_path)
        if snapshot is None:
            return False
        # Confirm the host path is actually bind-mounted at /workspace.
        mounted = any(
            m.type == "bind"
            and m.source == host_path
            and m.destination == CONTAINER_WORKSPACE
            for m in self._mounts.get(container_name, [])
        )
        if not mounted:
            return False
        return not (
            self.bd_bootstrap_ran(container_name)
            or self.shop_templates_update_ran(container_name)
        )
