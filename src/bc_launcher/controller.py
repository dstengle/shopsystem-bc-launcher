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

from bc_launcher.driver import ContainerMount, DockerDriver, RegistryDriver


# ---------------------------------------------------------------------------
# Constants / defaults
# ---------------------------------------------------------------------------

CONTAINER_WORKSPACE = "/workspace"
AGENT_TMUX_SESSION = "agent"
# The container user that owns the agent tmux session and all of its
# clients (send-keys, capture-pane, has-session, attach-session).  The
# BC image's default USER is root; Claude Code refuses
# --dangerously-skip-permissions when EUID==0 for security reasons, so
# the agent must run as a non-root user.  vscode is the unprivileged
# user already provisioned in the BC base image with HOME=/home/vscode
# (the same home into which credential mounts and cp steps land).
AGENT_CONTAINER_USER = "vscode"
BC_IMAGE = "ghcr.io/dstengle/shopsystem-bc-base:latest"
SHOPMSG_DSN_ENV = "SHOPMSG_DSN"

# ---------------------------------------------------------------------------
# Agent-vault credential broker model (ADR-026, lead-hxb8 / lead-v4ih)
# ---------------------------------------------------------------------------
#
# ADR-026 (accepted 2026-06-09) locks the credential disposition: ZERO
# host-filesystem credential coupling reaches a BC container, for BOTH Claude
# OAuth and GitHub.  The agent-vault broker is the SOLE credential path; there
# is no launch-mode flag and no host-mount fallback.
#
# The launcher therefore:
#   * mounts NO host ~/.claude, ~/.config/gh, or ~/.gitconfig into the
#     container, and never consults BCLAUNCHER_HOST_HOME to resolve a
#     credential mount source;
#   * mounts a placeholder-only, read-only .credentials.json whose accessToken
#     is the literal AGENT_VAULT_PLACEHOLDER_TOKEN — never a real OAuth token;
#   * wraps the agent invocation as `agent-vault run -- claude`, with
#     HTTPS_PROXY pointed at the broker's proxy listener on the shopsystem
#     network, so the broker substitutes the real Claude OAuth and GitHub
#     credentials on outbound requests (the container never holds them);
#   * gates launch on an agent_vault_reachable readiness barrier alongside the
#     messaging-database barrier: the agent engages only when BOTH are
#     reachable.

# The literal accessToken value baked into the container's placeholder
# .credentials.json.  Scenarios 3931e43e / e4348b11 pin this exact string.
AGENT_VAULT_PLACEHOLDER_TOKEN = "__PLACEHOLDER__"

# Container path at which the placeholder Claude credential file lives and the
# proxy env var the agent-vault run wrapper honours for outbound substitution.
AGENT_VAULT_PROXY_ENV = "HTTPS_PROXY"

# Default agent-vault broker proxy listener address on the shopsystem network.
# The broker is provisioned out of band (scenario 2a4e9889); the launcher only
# needs its proxy-listener address to point HTTPS_PROXY at it and to probe
# readiness.  Overridable per-launch via the launch() ``agent_vault_broker``
# argument or the BCLAUNCHER_AGENT_VAULT_BROKER env var.
DEFAULT_AGENT_VAULT_BROKER = "http://agent-vault:14321"
AGENT_VAULT_BROKER_ENV = "BCLAUNCHER_AGENT_VAULT_BROKER"

# ---------------------------------------------------------------------------
# Operator-supplied agent-vault credential + TLS-trust injection
# (bclaunch-5hi / bclaunch-7pf)
# ---------------------------------------------------------------------------
#
# The in-container `agent-vault run` client authenticates to the broker using
# an addr + token + vault triple.  These are OPERATOR-SUPPLIED at launch (from
# the CLI --env-file / process env / launch() params) and are injected into
# the container env under these names.  The TOKEN value is NEVER a literal
# baked into source — the only credential literal permitted in src/ is the
# AGENT_VAULT_PLACEHOLDER_TOKEN above.
AGENT_VAULT_ADDR_ENV = "AGENT_VAULT_ADDR"
AGENT_VAULT_TOKEN_ENV = "AGENT_VAULT_TOKEN"
AGENT_VAULT_VAULT_ENV = "AGENT_VAULT_VAULT"

# The broker CA travels as an env var (bclaunch-7pf REVISED, operator design
# directive — supersedes the 9ca2e05 CA bind-mount).  The CA is a PUBLIC
# ~574-byte cert (NOT secret).  A controller-side CA bind-mount is UNSAFE under
# nested-docker / host-path mismatch and the design goal is to eliminate
# controller bind mounts entirely.  So the operator supplies the CA PEM via
# --env-file as AGENT_VAULT_CA_PEM; the controller injects it into the
# container env, and the bc-base entrypoint (bclaunch-9rr) materializes it to a
# file and exports the TLS-trust vars.  The controller does NO CA handling and
# builds NO CA bind-mount.
AGENT_VAULT_CA_PEM_ENV = "AGENT_VAULT_CA_PEM"

# The fixed container path at which the bc-base entrypoint materializes the CA
# from AGENT_VAULT_CA_PEM.  Retained here only so tests/scenarios can name the
# (former) controller-side path and assert the controller builds NO mount at
# it; the controller itself no longer references it.
CONTAINER_BROKER_CA_PATH = "/etc/agent-vault/broker-ca.pem"

# The container-internal path of the placeholder Claude credentials file.
CONTAINER_CLAUDE_CREDENTIALS_PATH = "/home/vscode/.claude/.credentials.json"

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


def beads_prefix_for(bc_name: str) -> str:
    """Derive the beads issue_prefix this BC's registry is expected to carry.

    A BC named ``shopsystem-<identifier>`` keeps its beads under a prefix
    derived from the identifier: lowercase, non-alphanumerics stripped.  The
    ``shopsystem-`` namespace prefix is dropped so the resulting registry
    prefix is the short BC identifier (e.g. ``shopsystem-messaging`` →
    ``messaging``).  This is the prefix the launcher configures inside the
    container's ``.beads`` so ``bd create`` yields ids carrying it.
    """
    ident = bc_name
    if ident.startswith("shopsystem-"):
        ident = ident[len("shopsystem-"):]
    ident = re.sub(r"[^a-z0-9]", "", ident.lower())
    return ident


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


class ManifestProductTypeError(Exception):
    """Raised when a bc-manifest.yaml file's `product:` field is not a string.

    Carries enough structured context that the CLI can format a single-line
    error message naming the field, file path, expected type, and observed
    type — without exposing the underlying ``AttributeError`` that would
    otherwise surface from ``_slugify`` downstream.

    Per lead-393: the launch path must convert this into a non-zero exit
    with a clean stderr message; a Python traceback is only acceptable when
    the operator opts in via ``--debug`` (or ``BCLAUNCHER_DEBUG=1``).
    """

    def __init__(
        self,
        manifest_path: Path,
        observed_type: str,
        *,
        field: str = "product",
        expected_type: str = "string",
    ) -> None:
        self.manifest_path = manifest_path
        self.field = field
        self.expected_type = expected_type
        self.observed_type = observed_type
        super().__init__(self.format_message())

    def format_message(self) -> str:
        """Single-line stderr-ready message naming field, file, types."""
        return (
            f"bc-manifest.yaml: field {self.field!r} in {self.manifest_path} "
            f"has wrong type: expected {self.expected_type}, "
            f"got {self.observed_type}"
        )


def _read_product_from_manifest(manifest_path: Path) -> str | None:
    """Read the 'product' field from a bc-manifest.yaml file.

    Returns None if the file does not exist or has no 'product' key.
    Raises yaml.YAMLError on parse failure.
    Raises ManifestProductTypeError if 'product' is present but not a string.
    """
    import yaml
    if not manifest_path.exists():
        return None
    data = yaml.safe_load(manifest_path.read_text())
    if not isinstance(data, dict):
        return None
    if "product" not in data:
        return None
    product = data["product"]
    if product is None:
        # `product: null` (explicitly null) is just as broken as `product: 42`
        # for the downstream slugify call — name the field rather than silently
        # falling through to the "no network" branch, which would otherwise
        # hide the malformed-field root cause behind an unrelated error.
        raise ManifestProductTypeError(
            manifest_path=manifest_path,
            observed_type="null",
        )
    if not isinstance(product, str):
        raise ManifestProductTypeError(
            manifest_path=manifest_path,
            observed_type=type(product).__name__,
        )
    return product


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

    def __init__(
        self,
        driver: DockerDriver,
        registry_driver: RegistryDriver | None = None,
    ) -> None:
        self._driver = driver
        # Optional registry seam (scenario af2f03d3ac519cb5).  When present,
        # launch resolves the bc-base "latest" tag's current registry digest
        # BEFORE starting the container, and runs the container from that
        # resolved digest rather than whatever digest the local cache holds
        # under "latest".  Absent (the default), launch runs from BC_IMAGE as
        # before — the resolution step is purely additive.
        self._registry_driver = registry_driver

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
        agent_vault_broker: str | None = None,
        agent_vault_addr: str | None = None,
        agent_vault_token: str | None = None,
        agent_vault_vault: str | None = None,
        debug: bool = False,
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

        Credential model (ADR-026 — agent-vault broker, the SOLE path; CA via
        env var per the operator no-bind-mount directive):
        NO host credential directory or file is bind-mounted into the
        container, and the controller builds ZERO credential/CA bind mounts.
        ``BCLAUNCHER_HOST_HOME`` is not consulted to resolve any credential
        mount source.  Instead:
          - the placeholder-only ``.credentials.json`` whose ``accessToken`` is
            the literal ``"__PLACEHOLDER__"`` is BAKED INTO the bc-base image at
            ``/home/vscode/.claude/.credentials.json`` (never a real OAuth
            token; no controller mount);
          - the operator-supplied broker CA PEM travels as the
            ``AGENT_VAULT_CA_PEM`` container env var (supplied via
            ``--env-file``); the bc-base entrypoint materializes it to a file
            and exports the TLS-trust vars.  The controller does NO CA handling;
          - the agent is invoked wrapped as ``agent-vault run -- claude`` with
            ``HTTPS_PROXY`` pointed at the broker's proxy listener on the
            shopsystem network, so the broker substitutes the real Claude OAuth
            and GitHub credentials on outbound requests — the container itself
            never holds them.
        Launch is gated on an ``agent_vault_reachable`` readiness barrier
        (alongside the messaging-database barrier): the agent is engaged only
        when BOTH the messaging database AND the agent-vault broker are
        reachable.  ``agent_vault_broker`` overrides the broker proxy address
        (falling back to ``BCLAUNCHER_AGENT_VAULT_BROKER`` then the default).
        ``credential_home`` is retained only for staging the placeholder file
        in tests; no host credential is read from it.
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
            try:
                product = _read_product_from_manifest(effective_manifest)
            except ManifestProductTypeError as exc:
                # lead-393: a non-string `product:` (int / bool / null / list /
                # dict) must surface as a clean single-line stderr message
                # naming the field, file path, expected type, observed type —
                # NOT as the AttributeError that ``_slugify`` would raise on
                # the next line.  In debug mode the operator opts back into
                # the full traceback by letting the exception propagate.
                if debug:
                    raise
                return CommandResult(
                    exit_code=1,
                    stdout="",
                    stderr=exc.format_message() + "\n",
                )
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

        # --- Agent-vault broker resolution (ADR-026) ---
        # No host credential path is resolved; the broker is the sole
        # credential path.  Resolve the broker's proxy-listener address from
        # the explicit arg, then the env override, then the default.
        broker_address = (
            agent_vault_broker
            or os.environ.get(AGENT_VAULT_BROKER_ENV)
            or DEFAULT_AGENT_VAULT_BROKER
        )

        # Build environment
        env: dict[str, str] = {}
        env["HOME"] = "/home/vscode"
        # Route the agent's outbound HTTPS through the agent-vault broker's
        # proxy listener so the broker substitutes the real Claude OAuth and
        # GitHub credentials; the container itself carries none.
        env[AGENT_VAULT_PROXY_ENV] = broker_address

        # --- Operator-supplied agent-vault credentials (bclaunch-5hi) ---
        # The in-container `agent-vault run` client authenticates to the broker
        # with an addr + token + vault triple.  These are OPERATOR-SUPPLIED at
        # launch (explicit launch() params, sourced from the CLI --env-file /
        # flags / process env).  Each is injected only when supplied; the token
        # value is NEVER a literal baked into source.  Each value also falls
        # back to the like-named process-env var so an operator who exports
        # AGENT_VAULT_* (e.g. via --env-file piped into the launcher's own env)
        # does not have to also pass an explicit flag.
        resolved_av_addr = agent_vault_addr or os.environ.get(AGENT_VAULT_ADDR_ENV)
        resolved_av_token = agent_vault_token or os.environ.get(AGENT_VAULT_TOKEN_ENV)
        resolved_av_vault = agent_vault_vault or os.environ.get(AGENT_VAULT_VAULT_ENV)
        if resolved_av_addr:
            env[AGENT_VAULT_ADDR_ENV] = resolved_av_addr
        if resolved_av_token:
            env[AGENT_VAULT_TOKEN_ENV] = resolved_av_token
        if resolved_av_vault:
            env[AGENT_VAULT_VAULT_ENV] = resolved_av_vault

        # --- Broker CA via env var (bclaunch-7pf REVISED) ---
        # The operator supplies the PUBLIC broker CA PEM as an AGENT_VAULT_CA_PEM
        # line in --env-file (sourced into the launcher's process env).  Carry
        # it through into the container env so the bc-base entrypoint
        # (bclaunch-9rr) can materialize it to a file and export the TLS-trust
        # vars.  No CA bind-mount and no controller-side trust env are built —
        # the controller does ZERO CA handling beyond this pass-through.
        #
        # General rule: inject every AGENT_VAULT_* key present in the process
        # env into the container env (without clobbering an explicit-param
        # value set above), keeping the "token never baked / operator-supplied"
        # property — the launcher only forwards what the operator supplied.
        for key, value in os.environ.items():
            if key.startswith("AGENT_VAULT_") and key not in env:
                env[key] = value

        if shopmsg_dsn:
            env[SHOPMSG_DSN_ENV] = shopmsg_dsn
        elif dsn := os.environ.get(SHOPMSG_DSN_ENV):
            env[SHOPMSG_DSN_ENV] = dsn

        # --- Mounts (bclaunch-7pf REVISED: ZERO credential/CA bind mounts) ---
        # The placeholder .credentials.json is BAKED INTO the bc-base image
        # (no controller mount) and the broker CA travels as AGENT_VAULT_CA_PEM
        # (no controller mount).  The ONLY conditional mount remaining is the
        # SHOPMSG unix-socket mount below, which is functional transport, not
        # credential coupling.  Each entry: (type, source, dest, readonly).
        mounts: list[tuple[str, str, str, bool]] = []

        # SHOPMSG_DSN may be a postgres DSN (no socket mount needed) or a
        # unix socket path.  If the DSN value looks like a socket file, add a
        # bind mount for it.
        dsn_value = env.get(SHOPMSG_DSN_ENV, "")
        if dsn_value.startswith("/") and not dsn_value.startswith("//"):
            # It's a host socket path — mount the containing directory
            socket_dir = os.path.dirname(dsn_value)
            mounts.append(("bind", socket_dir, socket_dir, False))

        # Digest-resolution step (scenario af2f03d3ac519cb5).
        #
        # Before starting the container, resolve the bc-base "latest" tag's
        # CURRENT registry digest and run from that digest, so a republished
        # image reaches the new container instead of a stale locally-cached
        # "latest".  Without this step, a container started from the bare
        # "latest" tag would run whatever digest the local Docker cache holds
        # under "latest" (D_old) even after the registry has moved "latest"
        # to a newer digest (D_new).  When no registry driver is injected the
        # behaviour is unchanged: launch runs from BC_IMAGE.
        launch_image = BC_IMAGE
        if self._registry_driver is not None:
            resolved_digest = self._registry_driver.resolve_digest(BC_IMAGE)
            # Pin the run to the resolved digest.  A bare digest (sha256:...)
            # is turned into a fully-qualified digest reference against the
            # bc-base repository; an already-qualified reference is used as-is.
            if resolved_digest.startswith("sha256:"):
                repo = BC_IMAGE.split(":", 1)[0]
                launch_image = f"{repo}@{resolved_digest}"
            else:
                launch_image = resolved_digest

        self._driver.run(
            container_name=container,
            image=launch_image,
            env=env,
            mounts=mounts,
            network=resolved_network,
            detach=True,
        )

        out_lines: list[str] = [f"Started container {container}\n"]
        err_lines: list[str] = []

        # ADR-026: NO host gitconfig or .claude.json is copied into the
        # container.  GitHub and git identity flow through the agent-vault
        # broker on outbound requests; the only Claude credential file present
        # is the placeholder .credentials.json mounted read-only above.

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

            # Ensure beads is functionally usable inside the container by
            # configuring a non-empty issue_prefix in the workspace .beads.
            # A freshly cloned BC may land with its beads database missing
            # the issue_prefix config ("database not initialized:
            # issue_prefix config is missing"), which makes `bd create` fail
            # — the agent then cannot file its own follow-up findings.  The
            # launcher derives the BC's expected prefix and configures it so
            # `bd create` / `bd ready` work from first boot.
            prefix = beads_prefix_for(bc_name)
            self._driver.exec_run(
                container,
                ["bd", "config", "set", "issue_prefix", prefix],
            )
            out_lines.append(
                f"Configured beads issue_prefix {prefix!r} in container\n"
            )

            # Hand /workspace ownership to vscode.  The clone + bd dolt pull
            # exec_runs above default to root inside the BC image, leaving
            # /workspace and /workspace/.beads root-owned.  vscode then
            # cannot subsequently write (git commit, file edits, scenario
            # artifacts) — reproduced in lead-d64 as
            # `docker exec -u vscode bc-<bc> touch /workspace/.test` →
            # Permission denied.  This chown runs as root (the default) so
            # it can transfer ownership of files it does not yet own.
            self._driver.exec_run(
                container,
                ["chown", "-R", f"{AGENT_CONTAINER_USER}:{AGENT_CONTAINER_USER}",
                 CONTAINER_WORKSPACE],
            )
            out_lines.append(
                f"Chowned {CONTAINER_WORKSPACE} to {AGENT_CONTAINER_USER}\n"
            )

            # shop-templates pour (lead-dlrx, scenario 75ae95be0ecf1640).
            #
            # After the repository has been cloned (and the beads/ownership
            # setup steps have run), pour the shop-templates skill-group into
            # the cloned workspace so the launched BC shop carries its
            # ".claude/skills/" content from first boot.  The pour runs INSIDE
            # the container's workspace directory (the bc-base image carries the
            # shop-templates binary, installed from its VCS version pin) and
            # targets that same workspace dir, populating
            # ${CONTAINER_WORKSPACE}/.claude/skills/ with the shop-templates
            # skill-group.  Run as vscode so the poured files are owned by the
            # agent user (the chown above handed /workspace to vscode).
            self._driver.exec_run(
                container,
                ["shop-templates", "pour", "--workspace", CONTAINER_WORKSPACE],
                user=AGENT_CONTAINER_USER,
            )
            out_lines.append(
                f"Poured shop-templates skill-group into "
                f"{CONTAINER_WORKSPACE}/.claude/skills/\n"
            )

        # Start tmux session as vscode.  Claude Code refuses
        # --dangerously-skip-permissions when EUID==0 ("cannot be used with
        # root/sudo privileges for security reasons"), so the agent must
        # run as the unprivileged vscode user — and that requires the tmux
        # server itself to be vscode-owned, because tmux refuses
        # cross-user attach (any subsequent send-keys / capture-pane /
        # has-session / attach-session call against this session must
        # therefore also run as vscode).
        tmux_result = self._driver.exec_run(
            container,
            ["tmux", "new-session", "-d", "-s", AGENT_TMUX_SESSION],
            user=AGENT_CONTAINER_USER,
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
            # Readiness barrier — messaging database reachability.
            #
            # Before engaging the agent we verify the messaging backend at
            # SHOPMSG_DSN is reachable.  A BC agent whose messaging DB is
            # unreachable cannot arm its inbox watcher or drain pending
            # inbox, so injecting the startup prompt would launch an agent
            # straight into a wall of connection failures.  This barrier
            # fires BEFORE any Claude Code start / prompt injection: on
            # failure we return non-zero with a stderr line naming the DSN
            # and send NOTHING to the tmux session.
            dsn_for_readiness = env.get(SHOPMSG_DSN_ENV)
            if dsn_for_readiness and not self._driver.messaging_db_reachable(
                dsn_for_readiness
            ):
                err_lines.append(
                    f"messaging readiness failure: messaging database at "
                    f"{SHOPMSG_DSN_ENV}={dsn_for_readiness} is not reachable; "
                    f"startup prompt NOT injected\n"
                )
                return CommandResult(
                    exit_code=1,
                    stdout="".join(out_lines),
                    stderr="".join(err_lines),
                )

            # Readiness barrier — agent-vault broker reachability (ADR-026).
            #
            # The agent's Claude OAuth and GitHub credentials are substituted
            # by the agent-vault broker on outbound requests; an agent whose
            # broker is unreachable can authenticate to nothing.  This barrier
            # fires BEFORE any Claude Code start / prompt injection: on failure
            # we return non-zero with a stderr line naming the configured
            # broker address and send NOTHING to the tmux session.  Combined
            # with the messaging-DB barrier above, the agent engages only when
            # BOTH the messaging database AND the agent-vault broker are
            # reachable (scenarios f73afae0 / 64aaff80 / 6cb07698).
            if not self._driver.agent_vault_reachable(broker_address):
                err_lines.append(
                    f"agent-vault readiness failure: agent-vault broker at "
                    f"{broker_address} is not reachable; "
                    f"startup prompt NOT injected\n"
                )
                return CommandResult(
                    exit_code=1,
                    stdout="".join(out_lines),
                    stderr="".join(err_lines),
                )

            # Step 1: start Claude Code, wrapped as `agent-vault run -- claude`
            # (ADR-026).  agent-vault run establishes the proxy substitution
            # context (HTTPS_PROXY is already exported into the container env
            # pointing at the broker) so the broker injects the real Claude
            # OAuth / GitHub credentials on outbound requests; the container
            # holds only the placeholder.  --dangerously-skip-permissions is
            # passed through to claude: the BC container is the isolation
            # boundary the permission prompts substitute for, so bypassing
            # them inside the container prevents the agent from hanging on
            # permission gates that have no operator at the other end.
            self._driver.exec_run(
                container,
                ["tmux", "send-keys", "-t", AGENT_TMUX_SESSION,
                 "agent-vault run -- claude --dangerously-skip-permissions",
                 "Enter"],
                user=AGENT_CONTAINER_USER,
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
                user=AGENT_CONTAINER_USER,
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
            # Step 5: inject the startup prompt into Claude Code's input.
            #
            # Two DISCRETE send-keys invocations (text first, Enter second),
            # NOT one invocation carrying both (lead-lez1 / lead-9q0f root
            # cause).  A single `send-keys <text> Enter` exec_run concatenates
            # the whole keystream into ONE pty write() syscall; Claude Code's
            # TUI treats single-write payloads above ~70 bytes as a paste and
            # absorbs the trailing CR into the input buffer instead of
            # submitting.  Two exec_run calls are two discrete pty writes
            # separated by a kernel-scheduling gap, which the TUI processes as
            # a discrete submit keypress.
            self._driver.exec_run(
                container,
                ["tmux", "send-keys", "-t", AGENT_TMUX_SESSION, startup_prompt],
                user=AGENT_CONTAINER_USER,
            )
            self._driver.exec_run(
                container,
                ["tmux", "send-keys", "-t", AGENT_TMUX_SESSION, "Enter"],
                user=AGENT_CONTAINER_USER,
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
        # Attach as vscode: the agent tmux session is owned by vscode (see
        # launch()), and tmux refuses cross-user attach.
        self._driver.exec_interactive(
            container,
            ["tmux", "attach-session", "-t", AGENT_TMUX_SESSION],
            user=AGENT_CONTAINER_USER,
        )

    # ------------------------------------------------------------------
    # inject
    # ------------------------------------------------------------------

    def inject(self, bc_name: str, prompt_text: str) -> CommandResult:
        """Send text to the container's tmux session."""
        container = _container_name(bc_name)
        # send-keys against the vscode-owned tmux server must run as vscode.
        #
        # Two DISCRETE send-keys invocations (text first, Enter second), NOT
        # one invocation carrying both (lead-lez1 / lead-9q0f root cause).  A
        # single `send-keys <text> Enter` exec_run concatenates the whole
        # keystream into ONE pty write() syscall; Claude Code's TUI treats
        # single-write payloads above ~70 bytes as a paste and absorbs the
        # trailing CR into the input buffer instead of submitting.  Two
        # exec_run calls are two discrete pty writes separated by a
        # kernel-scheduling gap, which the TUI processes as a discrete submit.
        self._driver.exec_run(
            container,
            ["tmux", "send-keys", "-t", AGENT_TMUX_SESSION, prompt_text],
            user=AGENT_CONTAINER_USER,
        )
        self._driver.exec_run(
            container,
            ["tmux", "send-keys", "-t", AGENT_TMUX_SESSION, "Enter"],
            user=AGENT_CONTAINER_USER,
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
        # capture-pane against the vscode-owned tmux server must run as vscode.
        result = self._driver.exec_run(
            container,
            ["tmux", "capture-pane", "-p", "-t", AGENT_TMUX_SESSION],
            user=AGENT_CONTAINER_USER,
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

        # Check tmux session.  has-session against the vscode-owned tmux
        # server must run as vscode, same as every other tmux client call.
        tmux_result = self._driver.exec_run(
            container,
            ["tmux", "has-session", "-t", AGENT_TMUX_SESSION],
            user=AGENT_CONTAINER_USER,
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
    # readiness sequence (messaging-DB barrier, idempotent)
    # ------------------------------------------------------------------

    def ensure_ready(
        self,
        bc_name: str,
        shopmsg_dsn: str | None = None,
    ) -> CommandResult:
        """Run (or re-run) the messaging readiness sequence for the container.

        Idempotent: re-running against a container that has already passed
        its readiness sequence is a no-op that exits zero and reports the
        container is already ready — it does NOT re-send any startup prompt.

        Returns non-zero with a DSN-naming stderr line when the messaging
        database is unreachable.
        """
        container = _container_name(bc_name)
        dsn = shopmsg_dsn or os.environ.get(SHOPMSG_DSN_ENV)

        if self._container_marked_ready(container):
            return CommandResult(
                exit_code=0,
                stdout=f"{container} is already ready\n",
            )

        if dsn and not self._driver.messaging_db_reachable(dsn):
            return CommandResult(
                exit_code=1,
                stdout="",
                stderr=(
                    f"messaging readiness failure: messaging database at "
                    f"{SHOPMSG_DSN_ENV}={dsn} is not reachable\n"
                ),
            )

        self._mark_container_ready(container)
        return CommandResult(
            exit_code=0,
            stdout=f"{container} is ready\n",
        )

    # Readiness bookkeeping is delegated to the driver so the fake can model
    # an already-ready container.  Real drivers may persist this as a
    # container label / sentinel; the fake holds it in memory.
    def _container_marked_ready(self, container: str) -> bool:
        marker = getattr(self._driver, "is_marked_ready", None)
        return bool(marker(container)) if callable(marker) else False

    def _mark_container_ready(self, container: str) -> None:
        marker = getattr(self._driver, "mark_ready", None)
        if callable(marker):
            marker(container)

    # ------------------------------------------------------------------
    # health
    # ------------------------------------------------------------------

    def health(self, bc_name: str) -> CommandResult:
        """Report the BC container's Docker health status.

        The container is healthy only when beads is functionally usable
        inside it AND the messaging database at SHOPMSG_DSN is reachable;
        otherwise it is unhealthy even if the agent process is alive.  This
        mirrors the in-container healthcheck the launch wires up; the host
        reads the resulting status via ``docker inspect``.
        """
        container = _container_name(bc_name)
        status = self._driver.health_status(container)
        return CommandResult(
            exit_code=0 if status == "healthy" else 1,
            stdout=f"{status}\n",
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
