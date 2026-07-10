"""
Business logic for bc-container subcommands.

All Docker interaction goes through the DockerDriver interface, making this
layer fully testable without a live Docker daemon.
"""
from __future__ import annotations

import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from bc_launcher.driver import (
    ContainerMount,
    DockerDriver,
    DockerSocketUnreachableError,
    RegistryDriver,
)
from bc_launcher.constants import (  # shared primitives (single source of truth)
    AGENT_CONTAINER_USER,
    AGENT_VAULT_CONTAINER_CA_PATH,
    CONTAINER_WORKSPACE,
    SSL_CERT_FILE_ENV,
)

# ---------------------------------------------------------------------------
# Re-exported from bc_launcher.constants (Phase 1 controller.py decomposition)
# so historical `from bc_launcher.controller import <name>` paths resolve.
# ---------------------------------------------------------------------------
from bc_launcher.constants import (  # noqa: F401,E402
    DOCKER_SOCKET_PATH,
    AGENT_TMUX_SESSION,
    BC_IMAGE,
    BC_IMAGE_ENV,
    SHOPMSG_DSN_ENV,
)

# ---------------------------------------------------------------------------
# Re-exported from bc_launcher.diagnostics (Phase 1 controller.py decomposition)
# so historical `from bc_launcher.controller import <name>` paths resolve.
# ---------------------------------------------------------------------------
from bc_launcher.diagnostics import (  # noqa: F401,E402
    BCLAUNCHER_HOST_STATE_DIR_ENV,
    XDG_STATE_HOME_ENV,
    DEFAULT_HOST_STATE_DIR_LEAF,
    LAUNCH_DIAGNOSTIC_FILENAME,
    CAUSE_MARKER_MESSAGING_DB,
    CAUSE_MARKER_AGENT_VAULT,
    CAUSE_MARKER_READINESS,
    CAUSE_MARKER_AGENT_STARTUP,
    default_host_state_dir,
    launch_diagnostic_path,
    _resolve_host_path,
)

# ---------------------------------------------------------------------------
# Re-exported from bc_launcher.networking (Phase 1 controller.py decomposition)
# so historical `from bc_launcher.controller import <name>` paths resolve.
# ---------------------------------------------------------------------------
from bc_launcher.networking import (  # noqa: F401,E402
    SHOPMSG_SYSTEM_SLUG_ENV,
    DEFAULT_SYSTEM_SLUG,
    _resolve_shop_network,
    resolve_probe_broker_address,
)

# ---------------------------------------------------------------------------
# Re-exported from bc_launcher.tracker_provision (Phase 1 controller.py decomposition)
# so historical `from bc_launcher.controller import <name>` paths resolve.
# ---------------------------------------------------------------------------
from bc_launcher.tracker_provision import (  # noqa: F401,E402
    BEADS_REMOTE_ORG,
    TRACKER_PROVISION_GH_TOKEN,
    _beads_dolt_remote_url,
    _is_empty_remote_failure,
    _beads_dolt_repo_slug,
    _is_repo_not_found_failure,
    _create_absent_tracker_repo_script,
    _tracker_provision_exec_env,
    _empty_remote_seed_script,
    _resolve_origin_owner_writeback_script,
)

# ---------------------------------------------------------------------------
# Re-exported from bc_launcher.agent_vault (Phase 1 controller.py decomposition)
# so historical `from bc_launcher.controller import <name>` paths resolve.
# ---------------------------------------------------------------------------
from bc_launcher.agent_vault import (  # noqa: F401,E402
    AGENT_VAULT_PLACEHOLDER_TOKEN,
    AGENT_VAULT_PROXY_ENV,
    DEFAULT_AGENT_VAULT_BROKER,
    AGENT_VAULT_BROKER_ENV,
    AGENT_VAULT_CONTROL_API_PORT,
    AGENT_VAULT_SERVICE_NAME,
    AGENT_VAULT_MITM_PROXY_PORT,
    GIT_SSL_CAINFO_ENV,
    CA_PEM_FIRST_LINE,
    AGENT_VAULT_ADDR_ENV,
    AGENT_VAULT_TOKEN_ENV,
    AGENT_VAULT_VAULT_ENV,
    AGENT_VAULT_CA_PEM_ENV,
    CONTAINER_BROKER_CA_PATH,
    CONTAINER_CLAUDE_CREDENTIALS_PATH,
    _clone_ca_materialize_script,
    _mitm_proxy_host,
    _build_clone_proxy_url,
    _build_runtime_proxy_url,
)

# (SSL_CERT_FILE_ENV / AGENT_VAULT_CONTAINER_CA_PATH are shared primitives
# imported from bc_launcher.constants; the lead-ze4w BUG#3 rationale lives on
# _fabro_exec_env below.)


def _fabro_exec_env() -> dict[str, str]:
    """The extra exec env the launcher pins on the fabro shim + engage execs.

    lead-ze4w BUG#3: these execs run in a non-login `/bin/sh -c` that never
    sources /etc/profile.d/agent-vault-ca.sh, so SSL_CERT_FILE (the python /
    urllib CA-trust var) is empty and the shim's upstream HTTPS through
    HTTPS_PROXY fails CERTIFICATE_VERIFY_FAILED.  Set SSL_CERT_FILE explicitly
    to the SAME materialized broker CA path the clone path points git at via
    GIT_SSL_CAINFO, so the shim + engage trust the agent-vault MITM CA without
    a login shell.
    """
    return {SSL_CERT_FILE_ENV: AGENT_VAULT_CONTAINER_CA_PATH}

# ---------------------------------------------------------------------------
# Fabro def-bundle + orchestrator wiring — extracted to bc_launcher.fabro
# (bead shopsystem_bc_launcher-7pa4, Phase 1 controller.py decomposition).
# Re-exported here so the historical
# ``from bc_launcher.controller import <name>`` import paths keep resolving.
# ---------------------------------------------------------------------------
from bc_launcher.fabro import (  # noqa: E402,F401  (re-export for compat)
    FABRO_DEF_CONTAINER_DIR,
    FABRO_DEF_ASSET_SUBDIR,
    FABRO_DEF_FILES,
    _fabro_def_asset_root,
    _load_fabro_def_files,
    _fabro_def_install_script,
    LAUNCH_PATH_TMUX,
    LAUNCH_PATH_FABRO,
    ANTHROPIC_OAUTH_SHIM_BIN,
    FABRO_SHIM_HOST,
    FABRO_SHIM_PORT,
    FABRO_SETTINGS_CONTAINER_PATH,
    FABRO_WORKFLOW_TOML_CONTAINER_PATH,
    FABRO_WORKFLOW_TOML_DEFAULT_BC_NAME,
    FABRO_WORKFLOW_TOML_DEFAULT_WORK_ID,
    FABRO_ANTHROPIC_BASE_URL,
    FABRO_ANTHROPIC_ADAPTER,
    FABRO_BIN,
    FABRO_WORKFLOW_FILE,
    FABRO_DISPATCHER_FILE,
    FABRO_SERVER_INSTALL_GITHUB_USERNAME,
    FABRO_SERVER_INSTALL_GH_TOKEN,
    FABRO_SERVER_INSTALL_ARGV,
    FABRO_SERVER_DUMMY_ANTHROPIC_KEY,
    FABRO_SERVER_SETTINGS_CONTAINER_PATH,
    _fabro_server_start_argv,
    _fabro_server_install_argv,
    _fabro_run_argv,
    _fabro_engage_script,
    _fabro_shim_start_argv,
    _fabro_shim_start_script,
    _fabro_settings_toml,
    _fabro_settings_install_script,
    _fabro_workflow_toml_rewrite,
    _fabro_workflow_toml_install_script,
)

# ---------------------------------------------------------------------------
# Re-exported from bc_launcher.readiness (Phase 1 controller.py decomposition)
# so historical `from bc_launcher.controller import <name>` paths resolve.
# ---------------------------------------------------------------------------
from bc_launcher.readiness import (  # noqa: F401,E402
    CLAUDE_READY_MARKER,
    CLAUDE_INPUT_READY_MARKER,
    CLAUDE_READINESS_TIMEOUT_SECONDS,
    OPTION_SCREEN_MARKER,
    ESCAPE_AFFORDANCE_MARKER,
    ESCAPE_KEY_NAME,
    READINESS_PROMPT_ESCAPE_AFFORDANCE_MARKERS,
    WORKSPACE_TRUST_PROMPT_MARKERS,
    FULLSCREEN_RENDERER_PROMPT_MARKER,
    READINESS_DISMISS_POLL_SECONDS,
    _readiness_wait_blocking_prompt,
)

# ---------------------------------------------------------------------------
# Re-exported from bc_launcher.naming (Phase 1 controller.py decomposition)
# so historical `from bc_launcher.controller import <name>` paths resolve.
# ---------------------------------------------------------------------------
from bc_launcher.naming import (  # noqa: F401,E402
    _BEADS_ISSUE_ID_RE,
    _container_name,
    beads_prefix_for,
    committed_beads_prefix_from_registry,
    _slugify,
)

# ---------------------------------------------------------------------------
# Re-exported from bc_launcher.manifest (Phase 1 controller.py decomposition)
# so historical `from bc_launcher.controller import <name>` paths resolve.
# ---------------------------------------------------------------------------
from bc_launcher.manifest import (  # noqa: F401,E402
    ManifestProductTypeError,
    _resolve_manifest_remote,
    _read_product_from_manifest,
)


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
        monotonic=None,
    ) -> None:
        self._driver = driver
        # Injectable monotonic-clock seam (lead-cw7m).  The bounded
        # readiness-wait scan-dismiss loop budgets its TOTAL elapsed time
        # against this clock so the dismissal loop terminates at the 60s
        # readiness timeout rather than looping indefinitely.  Production
        # passes nothing (time.monotonic); tests inject a deterministic clock
        # to drive the bounded-timeout path without real wall-clock waits.
        self._monotonic = monotonic if monotonic is not None else time.monotonic
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
        image: str | None = None,
        startup_prompt: str | None = None,
        network: str | None = None,
        shop_network: str | None = None,
        manifest_path: Path | None = None,
        credential_home: Path | None = None,
        agent_vault_broker: str | None = None,
        agent_vault_addr: str | None = None,
        agent_vault_token: str | None = None,
        agent_vault_vault: str | None = None,
        workspace_mount: str | None = None,
        mount_docker_socket: bool = False,
        launch_path: str = LAUNCH_PATH_TMUX,
        work_id: str | None = None,
        debug: bool = False,
    ) -> CommandResult:
        """
        Start a Docker container for the named BC.

        Idempotent: if the container is already running, report and exit 0.

        Network resolution (in priority order — ADR-038 D3):
        1. If ``network`` is provided explicitly, use it as-is (no auto-create).
        2. Otherwise, read ``product:`` from bc-manifest.yaml (at ``manifest_path``
           or ``Path("bc-manifest.yaml")`` in CWD), slugify it, and use that as the
           network name.  If the network does not yet exist, create it first.
        3. Otherwise, fall back to ``shop_network`` — the shop's docker network
           name resolved from on-disk shop configuration (lead-ngzl).  When the
           manifest carries no shop-level network/product field, launch resolves
           the network from the shop's known on-disk config (the canonical
           network is ``shopsystem``, derived in the interim from the
           ``compose.yaml`` network / product slug — see
           ``_resolve_shop_network``) instead of hard-erroring.  If this network
           does not yet exist, create it first.
        4. Only when NEITHER an explicit ``network``, NOR a manifest product,
           NOR an on-disk ``shop_network`` is resolvable does launch return a
           non-zero "no network" error.  The error path is NARROWED (lead-ngzl):
           a manifest that merely lacks a product field no longer hard-errors so
           long as the shop network is resolvable on disk.

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

        # --- Manifest product: (shared middle tier — lead-53y0) ---
        # Read the DECLARED manifest ``product:`` ONCE, up front, so all three
        # identity surfaces (docker network name, BC-name-shape prefix, and the
        # injected SHOPMSG_SYSTEM_SLUG) derive from the ONE resolver with the
        # manifest product as the shared middle tier.  Reading it here (rather
        # than only inside the network branch) means an explicit --network does
        # NOT suppress system-slug derivation from manifest product:.
        #
        # lead-393 reconciliation: a non-string ``product:`` (int/bool/null/
        # list/dict) is fatal ONLY when the manifest product is actually NEEDED
        # — i.e. when --network was NOT supplied and the network must derive
        # from it.  An explicit --network short-circuits the manifest typecheck
        # (the operator is not relying on the manifest), so a malformed product:
        # under an explicit --network is swallowed and the system slug falls
        # back to its default rather than blocking the launch.
        effective_manifest = manifest_path or Path("bc-manifest.yaml")
        manifest_product: str | None
        try:
            manifest_product = _read_product_from_manifest(effective_manifest)
        except ManifestProductTypeError as exc:
            if network is not None:
                # Explicit --network: do not block on a malformed manifest
                # product; treat it as absent for the slug surface.
                manifest_product = None
            elif debug:
                # In debug mode the operator opts back into the full traceback.
                raise
            else:
                # Network MUST derive from manifest product but it is malformed:
                # surface a clean single-line stderr message naming the field,
                # file path, expected type, observed type — NOT the
                # AttributeError that ``_slugify`` would raise downstream.
                return CommandResult(
                    exit_code=1,
                    stdout="",
                    stderr=exc.format_message() + "\n",
                )

        # --- Network resolution ---
        # The docker network name derives from the SAME product resolver
        # (lead-53y0 unification): there is no pre-existing per-surface env
        # override for the network, so the network product is
        #   manifest product: > default — slugified.
        resolved_network: str | None = network
        auto_create_network = False

        if resolved_network is None:
            if manifest_product:
                resolved_network = _slugify(manifest_product)
                auto_create_network = True
            elif shop_network:
                # On-disk shop network fallback (lead-ngzl, ADR-038 D3): when
                # the manifest carries no shop-level network/product field,
                # resolve the network from the shop's known on-disk config
                # rather than hard-erroring.  Same auto-create semantics as a
                # manifest-derived network.
                resolved_network = shop_network
                auto_create_network = True
            else:
                # NARROWED error path (lead-ngzl): fire ONLY when NEITHER an
                # on-disk shop network NOR --network is resolvable — NOT merely
                # because the manifest lacks a product field.
                return CommandResult(
                    exit_code=1,
                    stdout="",
                    stderr="no network: bc-manifest.yaml not found and --network not provided\n",
                )

        # Create the derived network if it does not yet exist (only for auto-derived, not explicit)
        if auto_create_network and not self._driver.network_exists(resolved_network):
            self._driver.network_create(resolved_network)

        # --- Repo-source resolution (lead-uiwu FACET 1) ---
        # The clone source is resolved with this precedence:
        #   1. an explicit ``--repo-url`` (``repo_url``) wins;
        #   2. an explicit ``--workspace-mount`` bind-mounts a host tree and
        #      SKIPS the clone entirely (handled in the mounts block below);
        #   3. otherwise — NO repo flags — RESOLVE the BC's git remote from
        #      bc-manifest.yaml (its per-BC ``remote:`` field; the manifest is
        #      "the declared source of remote URLs when launching BCs") and
        #      clone THAT into ``/workspace`` (scenario bdec2754d9135086).
        #
        # REGRESSION FIX: previously, a launch with neither ``--repo-url`` nor
        # ``--workspace-mount`` fell straight through to agent-start with an
        # EMPTY, non-git ``/workspace`` — no clone, no error (the silent empty
        # launch).  Now, when NO source is resolvable (no ``--repo-url``, no
        # ``--workspace-mount``, and no manifest remote for the BC), the launch
        # FAILS LOUDLY with a non-zero exit naming all three unresolvable
        # sources (scenario 0b50d090c9cc3c45) — it never silently succeeds with
        # an empty ``/workspace``.
        if repo_url is None and workspace_mount is None:
            resolved_remote = _resolve_manifest_remote(effective_manifest, bc_name)
            if resolved_remote:
                repo_url = resolved_remote
                out_lines_remote = (
                    f"Resolved repo remote for {bc_name!r} from "
                    f"{effective_manifest} (bc-manifest.yaml)\n"
                )
            else:
                return CommandResult(
                    exit_code=1,
                    stdout="",
                    stderr=(
                        f"no repo source for {bc_name!r}: could not resolve a "
                        f"clone source — neither --repo-url, --workspace-mount, "
                        f"nor a bc-manifest.yaml remote for {bc_name!r} was "
                        f"available; refusing to launch with an empty, non-git "
                        f"/workspace\n"
                    ),
                )
        else:
            out_lines_remote = None

        # --- Agent-vault broker resolution (ADR-026) ---
        # No host credential path is resolved; the broker is the sole
        # credential path.  Resolve the broker's CONTROL-API address from the
        # explicit arg, then the env override, then the default.  This address
        # is used for the readiness PROBE (agent_vault_reachable) below — the
        # control API on :14321 is the right target for a reachability check.
        # It is NOT, by itself, the container's runtime HTTPS_PROXY: that is
        # derived separately (bclaunch-3q12) to point at the :14322 MITM proxy.
        explicit_broker = agent_vault_broker or os.environ.get(
            AGENT_VAULT_BROKER_ENV
        )
        broker_address = explicit_broker or DEFAULT_AGENT_VAULT_BROKER

        # Build environment
        env: dict[str, str] = {}
        env["HOME"] = "/home/vscode"

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

        # --- Container runtime HTTPS_PROXY (bclaunch-3q12) ---
        # Route the agent's outbound HTTPS through the broker's MITM proxy
        # (:14322) so the broker substitutes the real Claude OAuth and GitHub
        # credentials; the container itself carries none.  Precedence: an
        # explicit operator-supplied broker URL (--agent-vault-broker /
        # BCLAUNCHER_AGENT_VAULT_BROKER) wins verbatim; otherwise the proxy is
        # DERIVED (:14322 + <token>:<vault> userinfo) from the env-file
        # AGENT_VAULT_ADDR/TOKEN/VAULT triple — the SAME derivation the brokered
        # clone uses.  Pre-3q12 this was set to the bare control-API address
        # (:14321), which is not an HTTPS-CONNECT MITM proxy, so the agent's
        # brokered calls failed (CONNECT tunnel failed / 405).
        runtime_proxy = _build_runtime_proxy_url(
            explicit_broker,
            resolved_av_addr,
            resolved_av_token,
            resolved_av_vault,
        )
        if runtime_proxy is not None:
            env[AGENT_VAULT_PROXY_ENV] = runtime_proxy
        else:
            # Neither an explicit broker nor a complete addr/token/vault triple
            # was supplied: fall back to the control-API default so the env var
            # is still present (e.g. for the HEALTHCHECK probe target), matching
            # the pre-3q12 behaviour for the no-credentials case.
            env[AGENT_VAULT_PROXY_ENV] = broker_address

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

        # --- SHOPMSG_SYSTEM_SLUG injection (lead-53y0) ---
        # RESOLVE the product slug and INJECT it into the launched container's
        # docker run env as -e SHOPMSG_SYSTEM_SLUG=<resolved>, MIRRORING the
        # SHOPMSG_DSN injection idiom directly above (env-dict entry -> the
        # FakeDockerDriver records it as a -e flag on the recorded run command).
        # bc-launcher NEVER reads/consumes SHOPMSG_SYSTEM_SLUG itself; the
        # CONSUMER is the BC's own shop-msg at runtime (messaging, lead-tgsb).
        #
        # Precedence for the injected slug (this surface's own override on top
        # of the shared manifest-product middle tier):
        #   SHOPMSG_SYSTEM_SLUG env on the launcher invocation
        #     > manifest product:
        #     > DEFAULT_SYSTEM_SLUG ('shopsystem').
        if env_system_slug := os.environ.get(SHOPMSG_SYSTEM_SLUG_ENV):
            resolved_system_slug = env_system_slug
        elif manifest_product:
            resolved_system_slug = manifest_product
        else:
            resolved_system_slug = DEFAULT_SYSTEM_SLUG
        env[SHOPMSG_SYSTEM_SLUG_ENV] = resolved_system_slug

        # --- Readiness PROBE broker address (lead-cs7k DEFECT (b)) ---
        # The agent-vault broker the READINESS PROBE targets must be reachable
        # from the launched container's product network.  Derive it from the
        # SAME resolved product slug injected above (so a second product probes
        # ``<slug>-agent-vault`` rather than the hardcoded ``agent-vault``),
        # DECOUPLED from the runtime HTTPS_PROXY built earlier (so this never
        # clobbers the token:vault@host:14322 derived proxy).  An explicit
        # operator broker still wins verbatim.
        probe_broker_address = resolve_probe_broker_address(
            explicit_broker, resolved_system_slug
        )

        # --- Mounts (bclaunch-7pf REVISED: ZERO credential/CA bind mounts) ---
        # The placeholder .credentials.json is BAKED INTO the bc-base image
        # (no controller mount) and the broker CA travels as AGENT_VAULT_CA_PEM
        # (no controller mount).  The ONLY conditional mount remaining is the
        # SHOPMSG unix-socket mount below, which is functional transport, not
        # credential coupling.  Each entry: (type, source, dest, readonly).
        mounts: list[tuple[str, str, str, bool]] = []

        # --- workspace-mount (lead-zxtk, @scenario_hash:0bc8e4532c04bf72 /
        #     9fc84c8424b2a223) ---
        # When the operator supplies an existing host working tree via
        # ``workspace_mount``, bind-mount that host path at the container's
        # /workspace and SKIP the clone (and ALL clone-path provisioning: no
        # bd bootstrap, no shop-templates re-pour).  This presents the live
        # host tree unchanged inside the container — its committed `.beads`
        # registry and poured `.claude/skills` are left byte-for-byte intact
        # because no provisioning step writes to the mounted tree.  The clone
        # block below is gated on ``repo_url and not workspace_mount`` so a
        # workspace-mount launch never reaches it.
        if workspace_mount:
            mounts.append(("bind", workspace_mount, CONTAINER_WORKSPACE, False))

        # --- opt-in lead-only docker-socket mount (lead-zxtk,
        #     @scenario_hash:ff370a4e7e9dac5e / e177655ba09a73fa) ---
        # The host docker socket is bind-mounted into the container ONLY when
        # the opt-in flag is enabled (a lead-only capability that lets the
        # launched shop drive docker itself).  By default the flag is absent
        # and NO docker-socket mount is added, so an ordinary BC container
        # carries no access to the host docker daemon.
        # lead-wdvx (Bug 1): the bind-mount alone grants NO usable access — the
        # container's non-root default user is not in the host socket's owning
        # group, so every docker call inside the container is rejected
        # permission-denied.  Resolve the HOST socket's actual gid (it varies
        # by host) and add it to the container's supplementary groups via
        # ``--group-add`` so the mounted socket is actually usable.  This is
        # gated on the SAME opt-in flag as the mount, so a launch WITHOUT the
        # flag grants no docker-socket group (guard against over-grant).
        docker_socket_group_add: list[str] = []
        if mount_docker_socket:
            mounts.append(
                ("bind", DOCKER_SOCKET_PATH, DOCKER_SOCKET_PATH, False)
            )
            resolver = getattr(self._driver, "host_socket_gid", None)
            gid = resolver(DOCKER_SOCKET_PATH) if callable(resolver) else None
            if gid is not None:
                docker_socket_group_add.append(str(gid))

        # SHOPMSG_DSN may be a postgres DSN (no socket mount needed) or a
        # unix socket path.  If the DSN value looks like a socket file, add a
        # bind mount for it.
        dsn_value = env.get(SHOPMSG_DSN_ENV, "")
        if dsn_value.startswith("/") and not dsn_value.startswith("//"):
            # It's a host socket path — mount the containing directory
            socket_dir = os.path.dirname(dsn_value)
            mounts.append(("bind", socket_dir, socket_dir, False))

        # Launch-image source resolution.
        #
        # Precedence (mirrors the SHOPMSG_DSN idiom above: flag -> env ->
        # default): an explicit ``image`` param (the --image flag) wins;
        # otherwise the BC_IMAGE process-env var; otherwise the built-in
        # BC_IMAGE constant.  This lets a launch target a base image other
        # than the hard-coded default without editing source, while leaving
        # the default behaviour unchanged when neither flag nor env is set.
        if image:
            resolved_image = image
        elif env_image := os.environ.get(BC_IMAGE_ENV):
            resolved_image = env_image
        else:
            resolved_image = BC_IMAGE

        # Digest-resolution step (scenario af2f03d3ac519cb5).
        #
        # Before starting the container, resolve the resolved image tag's
        # CURRENT registry digest and run from that digest, so a republished
        # image reaches the new container instead of a stale locally-cached
        # "latest".  Without this step, a container started from the bare
        # "latest" tag would run whatever digest the local Docker cache holds
        # under "latest" (D_old) even after the registry has moved "latest"
        # to a newer digest (D_new).  When no registry driver is injected the
        # behaviour is unchanged: launch runs from the resolved image.
        launch_image = resolved_image
        if self._registry_driver is not None:
            resolved_digest = self._registry_driver.resolve_digest(resolved_image)
            # Pin the run to the resolved digest.  A bare digest (sha256:...)
            # is turned into a fully-qualified digest reference against the
            # resolved image's repository; an already-qualified reference is
            # used as-is.
            if resolved_digest.startswith("sha256:"):
                repo = resolved_image.split(":", 1)[0]
                launch_image = f"{repo}@{resolved_digest}"
            else:
                launch_image = resolved_digest
            # Freshness at launch (af2f03d3ac519cb5): PULL the resolved digest
            # into the local cache BEFORE starting the container.  Resolving
            # alone is not enough — if the republished digest (D_new) is not
            # fetched, a run can still serve whatever content the local cache
            # holds under the moving "latest" tag (D_old).  Pulling the
            # digest-pinned reference guarantees the new container runs from
            # D_new, the republished image, rather than the stale cached D_old.
            self._driver.pull(launch_image)

        self._driver.run(
            container_name=container,
            image=launch_image,
            env=env,
            mounts=mounts,
            network=resolved_network,
            detach=True,
            group_add=docker_socket_group_add or None,
        )

        out_lines: list[str] = [f"Started container {container}\n"]
        err_lines: list[str] = []
        if out_lines_remote:
            out_lines.append(out_lines_remote)

        # ADR-026: NO host gitconfig or .claude.json is copied into the
        # container.  GitHub and git identity flow through the agent-vault
        # broker on outbound requests; the only Claude credential file present
        # is the placeholder .credentials.json mounted read-only above.

        # Clone repository if URL provided AND no workspace-mount is in effect.
        # lead-zxtk: a workspace-mount launch bind-mounts an existing host tree
        # at /workspace and must SKIP the clone AND all clone-path provisioning
        # (bd bootstrap, shop-templates re-pour) so the mounted tree's
        # `.beads`/`.claude/skills` stay byte-unchanged.  The entire clone +
        # provisioning block lives under this guard, so when workspace_mount is
        # set the launch proceeds straight to agent-start against the mounted
        # tree.
        if repo_url and not workspace_mount:
            # --- Launch-time clone trust env (bclaunch-5fji) ---
            # DEFECT 1: route the clone's HTTPS through the broker's MITM proxy
            # (:14322 with token:vault basic-auth) — NOT the bare control-API
            # address (:14321) that the container's HTTPS_PROXY env carries.
            # DEFECT 2: the clone runs in a non-login shell that never sources
            # /etc/profile.d/agent-vault-ca.sh, so set GIT_SSL_CAINFO explicitly
            # to the container CA path the bc-base entrypoint materializes.
            # Both are passed on the clone exec's own environment so the
            # brokered auto-clone succeeds without the operator-side
            # --agent-vault-broker full-URL workaround.
            clone_env: dict[str, str] = {}
            clone_proxy_url = _build_clone_proxy_url(
                resolved_av_addr, resolved_av_token, resolved_av_vault
            )
            if clone_proxy_url:
                clone_env[AGENT_VAULT_PROXY_ENV] = clone_proxy_url
                # http_proxy/https_proxy lowercase variants are honoured by some
                # libcurl/git builds; set the canonical HTTPS_PROXY plus the
                # lowercase https_proxy so the clone routes regardless.
                clone_env["https_proxy"] = clone_proxy_url

            # --- FACET 3 (lead-z0v2 — supersedes scenario 0d29c76818a323a1
            #     with scenario 09f871cf8b99a34b):
            #     ACTUALLY write the broker MITM root CA content BEFORE the
            #     clone, then point git at the SAME existing path.
            # The clone routes through HTTPS_PROXY at the agent-vault MITM proxy
            # (:14322), so container git must trust the broker's MITM root CA at
            # CLONE time — not only at agent-run time.
            #
            # REGRESSION (lead-z0v2): the prior implementation (a) ran the
            # entrypoint materializer (agent-vault-ca.sh), which writes the CA
            # file ONLY when AGENT_VAULT_CA_PEM is set, and (b) UNCONDITIONALLY
            # set GIT_SSL_CAINFO to the CA path.  In a real flagless launch
            # AGENT_VAULT_CA_PEM was EMPTY, so nothing was written, yet git was
            # still pointed at the path — the clone failed
            # "error setting certificate file: .../ca.pem".  That is a
            # write-path-vs-trust-path MISMATCH.
            #
            # FIX: run a single clone-prep script that writes real CA *content*
            # to AGENT_VAULT_CONTAINER_CA_PATH (from AGENT_VAULT_CA_PEM when
            # present, else `agent-vault ca fetch`) and verifies it is a
            # non-empty PEM (first line "-----BEGIN CERTIFICATE-----").  Only
            # when the file is confirmed present do we point GIT_SSL_CAINFO at
            # that SAME path.  If the prep fails, the launch fails LOUDLY rather
            # than handing git a CA path that does not exist.  Run as root so
            # the script can create the trust dir regardless of prior ownership.
            ca_prep = self._driver.exec_run(
                container,
                ["/bin/sh", "-c", _clone_ca_materialize_script()],
            )
            if ca_prep.returncode != 0:
                return CommandResult(
                    exit_code=1,
                    stdout="".join(out_lines),
                    stderr=(
                        "agent-vault broker CA materialization failed before "
                        "the clone; refusing to point git at a CA path that "
                        f"does not exist ({AGENT_VAULT_CONTAINER_CA_PATH}): "
                        f"{ca_prep.stderr}"
                    ),
                )
            # write-path == trust-path: point git at the SAME path we just wrote
            # and verified.  Never set this for a path the prep did not produce.
            clone_env[GIT_SSL_CAINFO_ENV] = AGENT_VAULT_CONTAINER_CA_PATH
            out_lines.append(
                "Materialized the agent-vault broker MITM root CA content to "
                f"{AGENT_VAULT_CONTAINER_CA_PATH} and pointed git at that same "
                "existing path before the clone (lead-z0v2 FACET 3)\n"
            )

            # --- FACET 2 (lead-uiwu, scenario 4154b0ea63d0516b):
            #     /workspace owned by the agent user BEFORE the clone.
            # REGRESSION FIX: in the v0.3.33 image /workspace is created
            # root:root (Dockerfile WORKDIR), while the agent + clone run as the
            # unprivileged vscode user (uid 1000).  A clone performed as vscode
            # into a root-owned /workspace fails "git clone failed: ...
            # /workspace/.git: Permission denied".  The working messaging
            # container has /workspace owned vscode:vscode.  So chown /workspace
            # to vscode FIRST (as root), THEN perform the clone AS vscode so the
            # non-root clone writes /workspace/.git without Permission denied.
            # This makes the operator workaround (`docker exec -u root chown
            # vscode:vscode /workspace` then clone as vscode) durable.
            # Recursive (-R) to coexist with the lead-d64 invariant that EVERY
            # /workspace chown is recursive; /workspace is empty at this
            # pre-clone point, so -R is harmless here while still delivering the
            # FACET 2 ownership precondition.
            self._driver.exec_run(
                container,
                ["chown", "-R",
                 f"{AGENT_CONTAINER_USER}:{AGENT_CONTAINER_USER}",
                 CONTAINER_WORKSPACE],
            )
            out_lines.append(
                f"Chowned {CONTAINER_WORKSPACE} to {AGENT_CONTAINER_USER} "
                "before the clone (lead-uiwu FACET 2)\n"
            )

            clone_result = self._driver.exec_run(
                container,
                ["git", "clone", repo_url, CONTAINER_WORKSPACE],
                user=AGENT_CONTAINER_USER,
                env=clone_env,
            )
            if clone_result.returncode != 0:
                return CommandResult(
                    exit_code=1,
                    stdout="".join(out_lines),
                    stderr=f"git clone failed: {clone_result.stderr}",
                )
            out_lines.append(f"Cloned {repo_url} into {CONTAINER_WORKSPACE}\n")

            # Provision the in-container beads tracker so the BC boots
            # WRITE-READY with NO manual heal (lead-ezzr — SUPERSEDES the
            # lead-kjv7 pull+config+import mechanism).
            #
            # A freshly cloned BC lands WEDGED: `.beads/issues.jsonl` is
            # git-tracked at HEAD but may be ABSENT from the working tree
            # (a gitignore hook), the Dolt working set is empty (no
            # `embeddeddolt/`), and no usable issue_prefix is set — so
            # `bd ready` / `bd create` fail.
            #
            # lead-ezzr ROOT CAUSE + FIX.  The lead-kjv7 mechanism
            # (`bd dolt pull` → `bd config set issue_prefix` → `bd import`)
            # was EMPIRICALLY BROKEN: the launched BC came up with
            # issue_prefix '(not set)' and `bd create` failed "database not
            # initialized: issue_prefix config is missing".  The explicit
            # `bd import` pushed embedded-Dolt into the lead-vlsu deadlock
            # ('database already exists' + no prefix) where every documented
            # non-destructive recovery refuses.  The deadlock was
            # SELF-INFLICTED: `bd dolt pull` FIRST creates an empty local DB
            # that makes a later `bd bootstrap` say "already exists, nothing
            # to do".
            #
            # PROVEN RECIPE (lead-verified in a real v0.2.7 container, per
            # bd's own `bd help init-safety` "ADOPTING A REMOTE ... use
            # `bd bootstrap`"): on a fresh clone with committed
            # `.beads/issues.jsonl` present and NO pre-existing bd-created
            # Dolt working set, `bd bootstrap` imports the git-tracked JSONL,
            # creates `.beads/embeddeddolt/`, and `bd create` / `bd ready`
            # then SUCCEED.  Fully NON-DESTRUCTIVE.  So:
            #
            #   * Do NOT run `bd dolt pull` first (it pre-creates the empty DB
            #     that deadlocks bootstrap).
            #   * Do NOT `bd config set issue_prefix` (bd rejects it; bootstrap
            #     derives the prefix from the imported registry).
            #   * Do NOT run a separate `bd import` that pre-creates the DB
            #     (that is the wedged lead-vlsu path).
            #
            # (1) Ensure the committed registry is present in the working
            # tree.  A git clone normally provides it, but a gitignore hook
            # can leave it absent; `git checkout HEAD -- .beads/issues.jsonl`
            # materializes it FIRST so bootstrap has git-tracked JSONL to
            # import.
            # ORDER IS LOAD-BEARING (lead-d64, empirically proven by real
            # container launch): chown the ENTIRE workspace to vscode FIRST,
            # then run BOTH the materialize and `bd bootstrap` AS VSCODE.
            #
            # The clone ran as root, so `.git` and the tree are root-owned.
            # Running `bd bootstrap` as ROOT corrupts the repo: it leaves the
            # working set + `.git` internals (e.g. `.git/logs/HEAD`) root-owned,
            # and the subsequent vscode agent's git operations then fail with
            # "Permission denied", collapsing the index into dozens of phantom
            # staged deletions of `.beads/*` and `.claude/*` and removing the
            # `.beads` tree entirely — bd ends up NON-write-ready.  Chowning
            # only `.beads`, or chowning AFTER a root-run bootstrap, does NOT
            # fix this.  Proven-clean recipe: chown-whole-workspace-FIRST +
            # materialize-and-bootstrap-AS-VSCODE → 0 phantom deletions,
            # write-ready bd, `.git` writable by the agent.

            # (1) Hand the ENTIRE workspace (including `.git`) to vscode.
            self._driver.exec_run(
                container,
                ["chown", "-R",
                 f"{AGENT_CONTAINER_USER}:{AGENT_CONTAINER_USER}",
                 CONTAINER_WORKSPACE],
            )
            out_lines.append(
                f"Chowned {CONTAINER_WORKSPACE} (including .git) to "
                f"{AGENT_CONTAINER_USER}\n"
            )

            # (2) Mark the workspace a safe git directory for the agent user
            # (its ownership just changed), then materialize the committed
            # registry AS VSCODE so bootstrap has git-tracked JSONL to import.
            self._driver.exec_run(
                container,
                ["git", "config", "--global", "--add",
                 "safe.directory", CONTAINER_WORKSPACE],
                user=AGENT_CONTAINER_USER,
            )
            self._driver.exec_run(
                container,
                ["git", "-C", CONTAINER_WORKSPACE,
                 "checkout", "HEAD", "--", ".beads/issues.jsonl"],
                user=AGENT_CONTAINER_USER,
            )
            out_lines.append(
                "Materialized committed .beads/issues.jsonl into the "
                "working tree\n"
            )

            # (3) `bd bootstrap` AS VSCODE — imports the git-tracked JSONL,
            # creates `.beads/embeddeddolt/`, leaves the BC write-ready.  The
            # ONLY provisioning command; no `bd dolt pull` before it (would
            # wedge it into a no-op), no `bd config set` (bd rejects it), no
            # separate `bd import`.  Run as vscode (NOT root) so the working
            # set and any git ops land vscode-owned and the repo stays clean.
            #
            # DEFENSIVE re-chown immediately before bootstrap: empirically,
            # `.beads` can be observed root-owned at bootstrap time in a real
            # launch even though step (1) chowned the whole tree (a re-root
            # whose cause does not reproduce in isolated exec replays).  bd
            # bootstrap run as vscode must `mkdir .beads/embeddeddolt`, which
            # fails "permission denied" on a root-owned `.beads`.  Re-asserting
            # vscode ownership here makes the step robust regardless of cause.
            self._driver.exec_run(
                container,
                ["chown", "-R",
                 f"{AGENT_CONTAINER_USER}:{AGENT_CONTAINER_USER}",
                 CONTAINER_WORKSPACE],
            )

            # lead-r34c / GAP B — RESOLVE the scaffolded ORIGIN_OWNER placeholder
            # to the derived GitHub owner and WRITE it into the in-container
            # `.beads` config sync.remote + the functional bd dolt remote BEFORE
            # `bd bootstrap` runs.  The scaffold was pushed with the literal
            # ORIGIN_OWNER placeholder (correct at scaffold time — no origin
            # owner was known yet); without this writeback `bd bootstrap` clones
            # the stale `git+https://github.com/ORIGIN_OWNER/<bc>-beads.git` URL
            # and fatal-fails "Repository not found" (observed live in David's
            # 2026-07-07 shopsystem-knowledge standup).  The owner is derived
            # from the CONTAINER's `/workspace` git origin (the real origin the
            # clone left behind), so by bootstrap time no literal ORIGIN_OWNER
            # segment survives in the functional remote and its clone target is
            # `<owner>/<bc>-beads`.  Best-effort: a config already carrying a
            # resolved owner makes this an idempotent no-op.  Run as vscode (the
            # workspace + `.beads` are vscode-owned after the chown above) so the
            # `.beads` writes stay agent-usable.
            self._driver.exec_run(
                container,
                ["bash", "-lc",
                 _resolve_origin_owner_writeback_script(bc_name)],
                user=AGENT_CONTAINER_USER,
            )

            boot_result = self._driver.exec_run(
                container,
                ["bash", "-lc",
                 f"cd {CONTAINER_WORKSPACE} && bd bootstrap"],
                user=AGENT_CONTAINER_USER,
            )

            # lead-7jc2 — ABSENT-REPO PROVISIONING.  When standing up a NEW BC
            # whose `<bc>-beads` GitHub tracker repo does NOT EXIST at all,
            # `bd bootstrap`'s clone fails "Repository not found" — a strictly
            # earlier failure than the empty-but-existing remote's "git remote
            # has no branches".  Observed live: this stranded the create-bc
            # path even when the sync.remote owner was correct.  The fix is to
            # CREATE the absent repo (with an initial branch/commit) BEFORE the
            # empty-remote seed step, INSTEAD of fatal-failing the launch.
            # After creation the repo EXISTS but its Dolt remote is still
            # uninitialized, so a retried bootstrap then falls into the
            # lead-5k8c empty-remote seed path below, which adds the
            # `bd dolt remote` and pushes the working set.
            if boot_result.returncode != 0 and _is_repo_not_found_failure(
                boot_result.stderr or boot_result.stdout or ""
            ):
                beads_repo_slug = _beads_dolt_repo_slug(bc_name)
                create_result = self._driver.exec_run(
                    container,
                    ["bash", "-lc",
                     _create_absent_tracker_repo_script(beads_repo_slug)],
                    user=AGENT_CONTAINER_USER,
                    # lead-3mez / GAP A: carry a non-empty GH_TOKEN placeholder
                    # so `gh repo create` authenticates through the agent-vault
                    # proxy (which substitutes the real GITHUB_TOKEN on the
                    # wire) instead of exiting non-zero with "gh auth login" /
                    # "populate GH_TOKEN" and never creating the tracker repo.
                    env=_tracker_provision_exec_env(),
                )
                if create_result.returncode == 0:
                    out_lines.append(
                        "Absent beads tracker repo detected; created "
                        f"{beads_repo_slug} with an initial branch/commit and "
                        "retried bd bootstrap (lead-7jc2)\n"
                    )
                    # Re-assert vscode ownership, then retry bootstrap; the
                    # now-created-but-empty remote falls into the empty-remote
                    # seed path below.
                    self._driver.exec_run(
                        container,
                        ["chown", "-R",
                         f"{AGENT_CONTAINER_USER}:{AGENT_CONTAINER_USER}",
                         CONTAINER_WORKSPACE],
                    )
                    boot_result = self._driver.exec_run(
                        container,
                        ["bash", "-lc",
                         f"cd {CONTAINER_WORKSPACE} && bd bootstrap"],
                        user=AGENT_CONTAINER_USER,
                    )
                else:
                    err_lines.append(
                        "warning: beads tracker repo does not exist and could "
                        f"not be created ({beads_repo_slug}, exit "
                        f"{create_result.returncode}): "
                        f"{(create_result.stderr or create_result.stdout).strip()}; "
                        "proceeding to agent-start so the agent can self-heal "
                        "(lead-7jc2)\n"
                    )

            # lead-5k8c — EMPTY-REMOTE PROVISIONING.  A BC whose `<bc>-beads`
            # Dolt remote was never seeded (the GitHub repo exists but is
            # EMPTY) makes `bd bootstrap`'s clone fail:
            #   "dolt clone git+https://.../<bc>-beads.git: git remote has no
            #    branches: cannot push...; initialize the repository with an
            #    initial branch/commit first".
            # Empirically observed live 2026-06-22 (lead-4qpq fleet relaunch):
            # this failure stranded a healthy cloned container with no agent.
            # The fix is to INITIALIZE the empty remote — seed it with an
            # initial branch/commit from the git-tracked `.beads/issues.jsonl`
            # — then retry bootstrap, INSTEAD of fatal-failing the launch.
            # The seed mirrors the heal performed live: `git init -b main` a
            # temp repo and push an initial commit to the `<bc>-beads.git`
            # GitHub repo (creds injected by the agent-vault proxy via
            # HTTPS_PROXY), then `bd dolt remote add origin` + `bd dolt push`.
            if boot_result.returncode != 0 and _is_empty_remote_failure(
                boot_result.stderr or boot_result.stdout or ""
            ):
                beads_remote = _beads_dolt_remote_url(bc_name)
                seed_result = self._driver.exec_run(
                    container,
                    ["bash", "-lc", _empty_remote_seed_script(beads_remote)],
                    user=AGENT_CONTAINER_USER,
                )
                if seed_result.returncode == 0:
                    out_lines.append(
                        "Empty beads dolt remote detected; initialized it with "
                        f"an initial branch/commit ({beads_remote}) and retried "
                        "bd bootstrap (lead-5k8c)\n"
                    )
                    # Re-assert vscode ownership before the retry (the seed may
                    # have re-rooted paths under .beads), then retry bootstrap.
                    self._driver.exec_run(
                        container,
                        ["chown", "-R",
                         f"{AGENT_CONTAINER_USER}:{AGENT_CONTAINER_USER}",
                         CONTAINER_WORKSPACE],
                    )
                    boot_result = self._driver.exec_run(
                        container,
                        ["bash", "-lc",
                         f"cd {CONTAINER_WORKSPACE} && bd bootstrap"],
                        user=AGENT_CONTAINER_USER,
                    )
                else:
                    err_lines.append(
                        "warning: beads dolt remote is empty and could not be "
                        f"initialized ({beads_remote}, exit "
                        f"{seed_result.returncode}): "
                        f"{(seed_result.stderr or seed_result.stdout).strip()}; "
                        "proceeding to agent-start so the agent can self-heal "
                        "(lead-5k8c)\n"
                    )

            if boot_result.returncode != 0:
                # lead-5k8c — NO PRE-AGENT-START STEP MAY FATAL-STRAND THE
                # CONTAINER.  This extends the lead-k4k7 warn-and-continue
                # pattern (originally applied to the shop-templates
                # skill-refresh) to the bd-bootstrap step.  A failed
                # `bd bootstrap` used to `return CommandResult(exit_code=1)`
                # BEFORE the tmux/claude agent-start step, leaving a fully
                # cloned "Up (healthy)" container with NO agent — a
                # non-resumable strand (observed live 2026-06-22).  Beads
                # provisioning is a boot convenience, NOT a precondition for
                # the agent to run: the BC's session-start beads-health step
                # self-heals a wedged tracker.  So a bootstrap failure now
                # WARNS and PROCEEDS to agent-start instead of aborting, so a
                # healthy cloned container is NEVER left without an agent.
                err_lines.append(
                    "warning: bd bootstrap failed while provisioning the "
                    "in-container bd working set (exit "
                    f"{boot_result.returncode}): "
                    f"{(boot_result.stderr or boot_result.stdout).strip()}; "
                    "the beads tracker may need a session-start heal but the "
                    "agent will still be started (lead-5k8c)\n"
                )
            else:
                out_lines.append(
                    "Ran bd bootstrap (imported git-tracked .beads/issues.jsonl "
                    "into the embedded-Dolt working set)\n"
                )

            # shop-templates skill-refresh (lead-dlrx scenario
            # 75ae95be0ecf1640; lead-q5k7 bugfix).
            #
            # After the repository has been cloned (and the beads/ownership
            # setup steps have run), re-pour the shop-templates skill-group
            # OVER the cloned workspace's committed `.claude/skills/` so the
            # launched shop carries the CURRENT package skills from first boot
            # (e.g. the lead-80t0 beads-health step), OVERWRITING any stale
            # committed copy in the BC's own repo.
            #
            # lead-q5k7 ROOT CAUSE + FIX.  The prior invocation execed
            # `shop-templates pour --workspace <ws>`, but `shop-templates`
            # has NO `pour` subcommand (valid: list/show/bootstrap/update)
            # and the flag is `--target`, not `--workspace`.  That exec
            # FAILED on every launch, yet the step appended a "Poured ..."
            # success line WITHOUT checking the result — a false-success log
            # that hid the failure, so the refresh silently NEVER ran and a
            # launched BC kept its committed-stale `.claude/skills/`.
            #
            # The correct invocation in the bc-base image is
            # `shop-templates update --target <ws> --shop-type <bc|lead>`.
            # The shop-type is derived from the cloned shop's own
            # `.claude/shop/type.md` (the canonical per-shop marker:
            # contents "bc" or "lead"); it must match the original
            # bootstrap.  Run as vscode so the refreshed files are owned by
            # the agent user (the chown above handed /workspace to vscode).
            # DEFENSIVE re-chown before the refresh: `shop-templates update`
            # runs as vscode and OVERWRITES `.claude/...` files (e.g.
            # canonical/bc-primer.md); a root-owned committed copy makes that
            # write fail "permission denied".  Re-assert vscode ownership so
            # the refresh can overwrite, regardless of any re-root upstream.
            self._driver.exec_run(
                container,
                ["chown", "-R",
                 f"{AGENT_CONTAINER_USER}:{AGENT_CONTAINER_USER}",
                 CONTAINER_WORKSPACE],
            )
            shop_type = self._read_shop_type(container)
            refresh_result = self._driver.exec_run(
                container,
                ["shop-templates", "update",
                 "--target", CONTAINER_WORKSPACE,
                 "--shop-type", shop_type],
                user=AGENT_CONTAINER_USER,
            )
            if refresh_result.returncode != 0:
                # lead-k4k7 — DOWNGRADE a skill-refresh failure from a fatal
                # early-return to a WARNING that still PROCEEDS to agent-start.
                #
                # The skill-refresh is a freshness nicety, not a precondition
                # for the agent to run: a stale-but-present skill set is
                # strictly better than a healthy container with no agent at all.
                # Previously a transient (network-blip) non-zero exit here did
                # `return CommandResult(exit_code=1)` BEFORE the tmux/claude
                # start, leaving a fully-cloned "Up (healthy)" container with no
                # agent session — a non-resumable strand that every relaunch
                # re-clones and re-strands (observed live 2026-06-19, blocking
                # 8 dispatches).  We now WARN and fall through to agent-start.
                #
                # The lead-q5k7 criterion-B invariants are preserved: we still
                # CHECK the result (no false-success "Refreshed ..." line on a
                # failure), and a failed refresh still deposits NO skills.  The
                # only change is the disposition — warn-and-continue instead of
                # fatal-abort.
                err_lines.append(
                    "warning: shop-templates skill-refresh failed "
                    f"(shop-type={shop_type!r}, exit "
                    f"{refresh_result.returncode}): "
                    f"{refresh_result.stderr.strip()}; the skill set may be "
                    "stale but the agent will still be started "
                    "(lead-k4k7)\n"
                )
            else:
                out_lines.append(
                    f"Refreshed shop-templates skill-group "
                    f"(shop-type={shop_type}) into "
                    f"{CONTAINER_WORKSPACE}/.claude/skills/\n"
                )

            # Self-contained fabro loop def bundle placement (lead-h2bj — S2
            # def-bundle delivery, ADR-051).  Placed on EVERY clone launch
            # (tmux AND fabro) so the cloned container carries the def runnable
            # FROM THE DEF ALONE.  On the fabro path the ADDITIONAL wiring
            # (workflow.toml rewrite + shim + settings) runs OUTSIDE this guard
            # via _place_fabro_def_and_wiring(place_def=False) — see lead-ze4w
            # BUG#1 below.  Runs BEFORE the FINAL ownership assertion so the
            # final chown hands the freshly-placed .fabro/ tree to the agent.
            self._place_fabro_def_bundle(container, out_lines, err_lines)

            # FINAL ownership assertion (lead-mf15, scenario
            # @scenario_hash:d9e4ce60e03df361).  TIGHTENS the lead-d64 /
            # lead-ezzr chowns: those run BEFORE the last root-context
            # provisioning op (the shop-templates refresh just above), so a
            # path that op — or any root-context op the container fires around
            # it — creates/re-roots is left root-owned with no later chown to
            # correct it before the agent engages.  Observed twice 2026-06-18:
            # `.beads` cloned root-owned at bring-up and `.git/objects/7e/`
            # re-rooted mid-run, each requiring a host
            # `docker exec -u root chown -R 1000:1000`.
            #
            # This chown is the LAST thing the launcher does under /workspace
            # before starting the agent's tmux session, so the ownership
            # snapshot the vscode agent inherits is UNCONDITIONALLY
            # vscode-owned across EVERY agent-touched path (/workspace, .git,
            # .beads) regardless of any intermediate re-root — no host-side
            # chown is ever needed.  It runs as root (the default — no `-u`)
            # because transferring ownership of any root-owned path requires
            # root.  This is additive: the chown-whole-workspace-first recipe
            # and the .beads-vscode-owned pin (2904f3a905567b48) continue to
            # hold; this only adds a final assertion AFTER the last
            # provisioning write.
            self._driver.exec_run(
                container,
                ["chown", "-R",
                 f"{AGENT_CONTAINER_USER}:{AGENT_CONTAINER_USER}",
                 CONTAINER_WORKSPACE],
            )
            out_lines.append(
                f"Final ownership assertion: re-chowned {CONTAINER_WORKSPACE} "
                f"(including .git and .beads) to {AGENT_CONTAINER_USER} after "
                f"the last provisioning op, before starting the agent\n"
            )

        # FABRO orchestrator wiring placement (lead-vwib; lead-ze4w BUG#1).
        #
        # lead-ze4w BUG#1 ROOT CAUSE + FIX.  The fabro shim/settings wiring (and
        # on the workspace-mount path the def bundle itself) previously lived
        # INSIDE the `if repo_url and not workspace_mount:` clone-only guard, so
        # on a `--workspace-mount --orchestrator fabro` launch it was SKIPPED
        # entirely — yet `_start_agent_session` still ran `fabro engage`, which
        # failed because /workspace/.fabro never existed.  This block is HOISTED
        # OUT of the clone guard so the fabro def + wiring is placed on BOTH the
        # clone AND the workspace-mount paths (before the engage).  It is GATED
        # on `launch_path == LAUNCH_PATH_FABRO`, so the tmux default path — on
        # either clone or workspace-mount — is UNCHANGED (no fabro writes, no
        # placement, tree presented unchanged).
        #
        # place_def: on the CLONE path the def bundle was ALREADY placed inside
        # the clone guard (lead-h2bj, every clone launch), so we do NOT re-place
        # it here — only the fabro-specific wiring runs.  On the WORKSPACE-MOUNT
        # path the clone guard never ran, so the def bundle must be placed here
        # too.  Placement runs BEFORE the engage in `_start_agent_session`, so
        # the fabro server + run find the placed def + settings.
        if launch_path == LAUNCH_PATH_FABRO:
            already_placed = bool(repo_url and not workspace_mount)
            self._place_fabro_def_and_wiring(
                bc_name,
                container,
                work_id,
                out_lines,
                err_lines,
                place_def=not already_placed,
            )

        # Agent-start sequence (shared with `start_agent`, lead-k4k7).  This is
        # the EXACT sequence the recovery subcommand drives so a stranded
        # container can be brought to a running agent without re-cloning; the
        # two readiness barriers and the inject-after-ready ordering stay
        # behaviorally identical across launch and start-agent because they run
        # the same code.
        return self._start_agent_session(
            bc_name,
            container,
            startup_prompt,
            env.get(SHOPMSG_DSN_ENV),
            probe_broker_address,
            out_lines,
            err_lines,
            launch_path=launch_path,
            work_id=work_id,
        )

    # ------------------------------------------------------------------
    # FABRO def + wiring placement (lead-h2bj / lead-vwib; hoisted out of the
    # clone-only guard by lead-ze4w BUG#1 so it runs on the workspace-mount
    # path too)
    # ------------------------------------------------------------------

    def _place_fabro_def_bundle(
        self,
        container: str,
        out_lines: list[str],
        err_lines: list[str],
    ) -> None:
        """Place the 15 packaged fabro loop def-bundle asset files into the
        launched container at FABRO_DEF_CONTAINER_DIR (lead-h2bj, ADR-051).

        Placed via a single exec_run running a base64-decode script (the driver
        exposes no `docker cp` seam), so each file lands byte-identical to the
        shipped asset regardless of content.  NATIVE-VAULT INVARIANT (ADR-049):
        the placed vaults/default/secrets.json is the `__PLACEHOLDER__`-only
        asset; placement introduces no real secret.  Run as vscode so the def
        is agent-owned; a failed placement is a boot-convenience warning, not a
        fatal abort that strands a healthy container with no agent.
        """
        def_files = _load_fabro_def_files()
        def_place_result = self._driver.exec_run(
            container,
            ["/bin/sh", "-c", _fabro_def_install_script(def_files)],
            user=AGENT_CONTAINER_USER,
        )
        if def_place_result.returncode != 0:
            err_lines.append(
                "warning: fabro loop def-bundle placement failed (exit "
                f"{def_place_result.returncode}): "
                f"{(def_place_result.stderr or def_place_result.stdout).strip()}"
                f"; the container may lack {FABRO_DEF_CONTAINER_DIR} but the "
                "agent will still be started (lead-h2bj)\n"
            )
        else:
            out_lines.append(
                f"Placed the self-contained fabro loop def bundle "
                f"({len(FABRO_DEF_FILES)} files) into "
                f"{FABRO_DEF_CONTAINER_DIR} (lead-h2bj, ADR-051)\n"
            )

    def _place_fabro_def_and_wiring(
        self,
        bc_name: str,
        container: str,
        work_id: str | None,
        out_lines: list[str],
        err_lines: list[str],
        place_def: bool = True,
    ) -> None:
        """Place the fabro-orchestrator wiring (workflow.toml identity rewrite
        + shim start + effective settings) — and, when ``place_def`` is True,
        the def bundle itself — into the launched container.

        lead-ze4w BUG#1: this runs on BOTH the clone and the workspace-mount
        launch paths (the caller invokes it OUTSIDE the clone-only guard), so a
        `--workspace-mount --orchestrator fabro` launch actually has
        /workspace/.fabro before `_start_agent_session` runs `fabro engage`.
        It is only invoked on the fabro path, so the tmux default is unchanged.

        ``place_def``: on the CLONE path the def bundle is placed inside the
        clone guard (every clone launch), so the caller passes place_def=False
        to avoid re-placing it.  On the WORKSPACE-MOUNT path the clone guard
        never ran, so place_def=True places the def here too.

        Steps (each a boot-convenience warn-and-continue on failure, mirroring
        the def-placement disposition — never a fatal abort that strands a
        healthy container with no agent):

          (0) PLACE the def bundle (only when ``place_def``; lead-h2bj).
          (1) REWRITE the placed workflow.toml BC_NAME / WORK_ID to the
              launch's ACTUAL bc_name / work_id (lead-ze4w BUG#2).
          (2) START the baked so2h shim as a background listener, with
              SSL_CERT_FILE pinned (lead-ze4w BUG#3).
          (3) WRITE fabro's effective workflow-level settings.toml pointing the
              anthropic provider at the shim; NO credential (ADR-049 D1/D2).
          (4) chown the placed .fabro/ tree to the agent user so the placed def
              + writes are agent-owned (on the workspace-mount path this touches
              ONLY the launcher-created .fabro/ subtree, never the mounted
              tree's committed .beads / .claude).
        """
        # (0) Place the def bundle when it was not already placed by the clone
        #     guard (workspace-mount path, lead-ze4w BUG#1).
        if place_def:
            self._place_fabro_def_bundle(container, out_lines, err_lines)

        # (1) Rewrite the placed workflow.toml's BC_NAME / WORK_ID to the
        #     launch's ACTUAL identity (lead-ze4w BUG#2).  The packaged asset
        #     carries the bundle defaults (fabro-throwaway / fabro-spike-demo-3)
        #     in BOTH [run.inputs] and [run.environment.env]; the native
        #     script= nodes read $BC_NAME / $WORK_ID from the [run.environment.
        #     env] overlay, and `fabro run -I` overrides only [run.inputs] for
        #     agent prompts — so without this rewrite every native node runs
        #     against the bundle identity.  Modeled on the settings.toml
        #     (re)write mechanism (host-side byte generation + base64-decode
        #     over the placed path).
        workflow_result = self._driver.exec_run(
            container,
            ["/bin/sh", "-c",
             _fabro_workflow_toml_install_script(bc_name, work_id or "")],
            user=AGENT_CONTAINER_USER,
        )
        if workflow_result.returncode != 0:
            err_lines.append(
                "warning: fabro workflow.toml BC_NAME/WORK_ID rewrite failed "
                f"(exit {workflow_result.returncode}): "
                f"{(workflow_result.stderr or workflow_result.stdout).strip()}"
                f"; {FABRO_WORKFLOW_TOML_CONTAINER_PATH} may carry the bundle "
                "defaults but the agent will still be started (lead-ze4w)\n"
            )
        else:
            out_lines.append(
                "Rewrote the placed "
                f"{FABRO_WORKFLOW_TOML_CONTAINER_PATH} [run.environment.env] / "
                f"[run.inputs] BC_NAME={bc_name} WORK_ID={work_id or ''} "
                "(lead-ze4w BUG#2)\n"
            )

        # (2) Start the baked so2h shim as a background listener, with
        #     SSL_CERT_FILE pinned on the exec env (lead-ze4w BUG#3) so its
        #     urllib trusts the agent-vault MITM CA without a login shell.
        shim_result = self._driver.exec_run(
            container,
            ["/bin/sh", "-c", _fabro_shim_start_script()],
            user=AGENT_CONTAINER_USER,
            env=_fabro_exec_env(),
        )
        if shim_result.returncode != 0:
            err_lines.append(
                "warning: anthropic-oauth-shim start failed (exit "
                f"{shim_result.returncode}): "
                f"{(shim_result.stderr or shim_result.stdout).strip()}"
                f"; fabro's anthropic provider may lack a local "
                f"endpoint on {FABRO_SHIM_HOST}:{FABRO_SHIM_PORT} but "
                "the agent will still be started (lead-vwib)\n"
            )
        else:
            out_lines.append(
                "Started the baked anthropic-oauth-shim "
                f"({ANTHROPIC_OAUTH_SHIM_BIN}) as a background "
                f"listener on {FABRO_SHIM_HOST}:{FABRO_SHIM_PORT} "
                "(lead-vwib, lead-so2h)\n"
            )

        # (3) Write fabro's effective workflow-level settings pointing the
        #     anthropic provider at the shim; no credential written (ADR-049).
        settings_result = self._driver.exec_run(
            container,
            ["/bin/sh", "-c", _fabro_settings_install_script()],
            user=AGENT_CONTAINER_USER,
        )
        if settings_result.returncode != 0:
            err_lines.append(
                "warning: fabro effective-settings write failed (exit "
                f"{settings_result.returncode}): "
                f"{(settings_result.stderr or settings_result.stdout).strip()}"
                f"; {FABRO_SETTINGS_CONTAINER_PATH} may be missing but "
                "the agent will still be started (lead-vwib)\n"
            )
        else:
            out_lines.append(
                "Wrote fabro effective settings to "
                f"{FABRO_SETTINGS_CONTAINER_PATH} "
                f"([llm.providers.anthropic] base_url="
                f"{FABRO_ANTHROPIC_BASE_URL}, adapter="
                f"{FABRO_ANTHROPIC_ADAPTER}; no credential written — "
                "ADR-049 D1/D2)\n"
            )

        # (4) Hand the placed .fabro/ tree to the agent user.  On the
        #     workspace-mount path this deliberately scopes the chown to the
        #     launcher-created .fabro/ subtree ONLY — it does NOT recursively
        #     chown the mounted host tree's committed .beads / .claude (the
        #     lead-zxtk byte-unchanged invariant).
        self._driver.exec_run(
            container,
            ["chown", "-R",
             f"{AGENT_CONTAINER_USER}:{AGENT_CONTAINER_USER}",
             FABRO_DEF_CONTAINER_DIR],
        )
        out_lines.append(
            f"Chowned the placed {FABRO_DEF_CONTAINER_DIR} tree to "
            f"{AGENT_CONTAINER_USER} (lead-ze4w BUG#1 workspace-mount parity)\n"
        )

    # ------------------------------------------------------------------
    # agent-start sequence (shared by launch + start_agent, lead-k4k7)
    # ------------------------------------------------------------------

    def _write_launch_diagnostic(
        self,
        bc_name: str,
        cause_marker: str,
        reason: str,
        err_lines: list[str],
    ) -> Path | None:
        """Persist a launch-failure diagnostic FILE on the per-BC host surface.

        lead-63em.  Writes a single human-readable line carrying the literal
        ``cause_marker`` token plus ``reason`` to the documented per-BC
        host-discoverable path (``launch_diagnostic_path``).  The file is
        readable from the host WITHOUT attaching into any tmux session and
        WITHOUT relying on the launch command's stderr or the bc-container
        monitor tmux pane.

        lead-bnhn (P1 bugfix) — BEST-EFFORT / NON-FATAL.  The diagnostic write
        (its on-demand parent ``mkdir`` and the file write) is wrapped so that
        ANY write failure (``PermissionError`` / ``OSError`` from an unwritable
        target dir, a read-only filesystem, etc.) is CAUGHT here, surfaced as a
        host-discoverable WARNING on the launch result's stderr (naming that
        the diagnostic could NOT be written, the target path, and the cause),
        and then SWALLOWED so the launch is NOT aborted.  A diagnostic-write
        failure is strictly less severe than the launch failure it would
        describe; it must degrade gracefully, never escalate.  This method is
        the single choke point ALL launch-failure-diagnostic call sites pass
        through, so wrapping it here protects EVERY call site at once.

        On success the method itself appends the host-discoverable
        ``launch diagnostic persisted to <path>`` line to ``err_lines`` and
        returns the path written; on a caught write failure it appends the
        warning line and returns ``None``.  The FILE, not the stderr line, is
        the authoritative diagnostic surface when the write succeeds — but the
        stderr warning is the legible fallback when even the file cannot be
        written.
        """
        path = launch_diagnostic_path(bc_name)
        content = (
            f"cause: {cause_marker}\n"
            f"reason: {reason}\n"
        )
        try:
            self._driver.write_launch_diagnostic(str(path), content)
        except OSError as exc:
            # NON-FATAL: the diagnostic write failed (e.g. the target dir is
            # not writable — the lead-bnhn /var/lib/bc-launcher PermissionError
            # crash).  Surface a host-discoverable WARNING and CONTINUE; never
            # let the diagnostic-write failure abort the launch it describes.
            err_lines.append(
                f"warning: could not write launch diagnostic to {path}: "
                f"{type(exc).__name__}: {exc}; continuing without the "
                f"persisted diagnostic file (the launch failure cause is "
                f"reported on stderr above)\n"
            )
            return None
        err_lines.append(f"launch diagnostic persisted to {path}\n")
        return path

    def _fabro_engage(
        self,
        bc_name: str,
        container: str,
        dsn: str | None,
        probe_broker_address: str,
        work_id: str | None,
        out_lines: list[str],
        err_lines: list[str],
    ) -> CommandResult:
        """Drive the FABRO orchestrator ENGAGE step (lead-cadr — S4, corrected
        by lead-odd9 / ADR-058).

        REPLACES the tmux/claude engage tier on the fabro launch path (ADR-050
        D3): AFTER the SAME readiness barriers the tmux path gates on
        (messaging DB + agent-vault broker — ADR-050 D1/D2 launch parity), the
        launcher engages by

          1. starting the EPHEMERAL in-container fabro server in the
             FOREGROUND with no web UI, bound to a local 127.0.0.1 socket
             (``fabro server start --foreground --no-web``), so the loop runs
             headless inside the one bc-base container; and
          2. running the placed REACTIVE-PERSISTENT DISPATCHER def against that
             server (``fabro run dispatcher.fabro -I BC_NAME=<bc>``) as the ONE
             persistent engage (ADR-058 D1).  It carries ONLY the constant
             BC_NAME and supplies NO ``-I WORK_ID``: the dispatcher OWNS the
             container's lifecycle and discovers work_ids at RUNTIME, fanning
             out one detached ``fabro run workflow.fabro`` child per pending
             work item.

        ``work_id`` is an IGNORED no-op on this path (ADR-058 D6): the fabro
        launch interface requires no launch-time work id, exactly like the tmux
        path.  It starts NO tmux ``agent`` send-keys session and NO ``claude``
        engage — the engage tier is REPLACED by the fabro run-graph entry, not
        added alongside it (reproduces fabro-orchestration/01
        @scenario_hash:1aeace4c593ab14f via the real bc-container launch path).
        """
        # Readiness barrier — messaging database reachability (IDENTICAL to the
        # tmux path — ADR-050 D1/D2 launch parity).  On failure engage NOTHING.
        if dsn and not self._driver.messaging_db_reachable(
            dsn, container=container
        ):
            reason = (
                f"messaging readiness failure: messaging database at "
                f"{SHOPMSG_DSN_ENV}={dsn} is not reachable; fabro engage NOT "
                f"started"
            )
            err_lines.append(reason + "\n")
            self._write_launch_diagnostic(
                bc_name, CAUSE_MARKER_MESSAGING_DB, reason, err_lines
            )
            return CommandResult(
                exit_code=1,
                stdout="".join(out_lines),
                stderr="".join(err_lines),
            )

        # Readiness barrier — agent-vault broker reachability (IDENTICAL to the
        # tmux path — ADR-026 / ADR-050 D1/D2 launch parity).
        if not self._driver.agent_vault_reachable(
            probe_broker_address, container=container
        ):
            reason = (
                f"agent-vault readiness failure: agent-vault broker at "
                f"{probe_broker_address} is not reachable; fabro engage NOT "
                f"started"
            )
            err_lines.append(reason + "\n")
            self._write_launch_diagnostic(
                bc_name, CAUSE_MARKER_AGENT_VAULT, reason, err_lines
            )
            return CommandResult(
                exit_code=1,
                stdout="".join(out_lines),
                stderr="".join(err_lines),
            )

        # ENGAGE (REPLACES the tmux/claude engage — ADR-050 D3).  One `/bin/sh
        # -c` script cd's into the placed def dir, starts the ephemeral fabro
        # server (foreground, no web, provider=local, 127.0.0.1), then runs the
        # loop def against it carrying BC_NAME + WORK_ID.  Runs as the vscode
        # agent user (the def + settings were placed agent-owned).  NO tmux
        # session is created and NO `claude` is started on this path.
        #
        # lead-lwk4 R7 (LAUNCH ACTUALLY RETURNS AFTER ENGAGE): issued DETACHED
        # (`docker exec -d`) so the docker daemon backgrounds the engage and this
        # call RETURNS IMMEDIATELY without reading the exec's stdout/stderr — the
        # foreground fabro server's stdio never rides the launcher's pipes, so
        # `launch()` returns after the engage is issued instead of blocking for
        # the server's lifetime.  The v0.3.49 nohup-inside-the-script fix could
        # not achieve this: backgrounded children inherit the (attached) exec
        # pipes, so a synchronous `docker exec` never sees EOF.  The fabro server
        # + run keep running headless in the container after this returns.
        engage_result = self._driver.exec_run(
            container,
            ["/bin/sh", "-c", _fabro_engage_script(bc_name)],
            user=AGENT_CONTAINER_USER,
            env=_fabro_exec_env(),
            detach=True,
        )
        if engage_result.returncode != 0:
            reason = (
                f"fabro engage failure: `fabro server start` / `fabro run "
                f"{FABRO_DISPATCHER_FILE}` exited {engage_result.returncode}: "
                f"{(engage_result.stderr or engage_result.stdout).strip()}"
            )
            err_lines.append("warning: " + reason + "\n")
            return CommandResult(
                exit_code=1,
                stdout="".join(out_lines),
                stderr="".join(err_lines),
            )
        out_lines.append(
            "Fabro orchestrator engage (lead-cadr / ADR-058): started the "
            "ephemeral in-container fabro server "
            f"({' '.join(_fabro_server_start_argv())}) and ran the PERSISTENT "
            "reactive dispatcher def as the engage ("
            f"{' '.join(_fabro_run_argv(bc_name))}); no tmux 'agent' send-keys "
            "session and no 'claude' engage started on this path — the engage "
            "tier is REPLACED by the fabro run-graph entry (ADR-050 D3)\n"
        )
        return CommandResult(
            exit_code=0, stdout="".join(out_lines), stderr="".join(err_lines)
        )

    def _start_agent_session(
        self,
        bc_name: str,
        container: str,
        startup_prompt: str | None,
        dsn: str | None,
        probe_broker_address: str,
        out_lines: list[str],
        err_lines: list[str],
        launch_path: str = LAUNCH_PATH_TMUX,
        work_id: str | None = None,
    ) -> CommandResult:
        """Drive the agent-start sequence against an already-provisioned
        container: start the agent tmux session, gate on the two readiness
        barriers, start ``agent-vault run -- claude``, wait for the readiness
        markers, and inject the startup prompt.

        SHARED by ``launch`` (after clone + provisioning) and ``start_agent``
        (recovery against an already-cloned container).  Sharing the sequence
        keeps the readiness barriers and inject ordering identical across both
        entry points (lead-k4k7).  ``out_lines`` / ``err_lines`` accumulate the
        result's stdout / stderr; the caller passes whatever preamble it has
        already logged.

        ENGAGE TIER (lead-cadr — S4).  ``launch_path`` selects the engage tier
        AFTER the readiness barriers pass; the barriers themselves and every
        launch-parity surface (container / credential-proxy / postgres DSN /
        shop-msg mailbox) are IDENTICAL on both paths (ADR-050 D1/D2):

          * ``launch_path == "tmux"`` (DEFAULT): the EXISTING tmux ``agent``
            send-keys / ``agent-vault run -- claude`` engage, UNCHANGED
            (scenario 04, @scenario_hash:04236074a60ffcd7).  NO fabro server,
            NO fabro run.
          * ``launch_path == "fabro"``: the engage tier is REPLACED (ADR-050
            D3) by the fabro run-graph entry — the launcher starts the
            ephemeral in-container fabro server
            (``fabro server start --foreground --no-web``) and runs the placed
            ADR-051 loop def against it
            (``fabro run workflow.fabro -I BC_NAME=<bc> -I WORK_ID=<work_id>``)
            as the engage, and starts NO tmux ``agent`` send-keys session and
            NO ``claude`` engage on this path.

        ``work_id`` carries the WORK_ID into the fabro run's ``-I`` input; it
        is unused on the tmux path.
        """
        # FABRO ORCHESTRATOR ENGAGE (lead-cadr).  On the fabro path the engage
        # tier is REPLACED, not added alongside (ADR-050 D3): the launcher
        # starts NO tmux `agent` send-keys session and NO `claude` engage.
        # The readiness barriers still gate the engage (identical to the tmux
        # path — ADR-050 D1/D2 launch parity): on failure, engage NOTHING.
        if launch_path == LAUNCH_PATH_FABRO:
            return self._fabro_engage(
                bc_name,
                container,
                dsn,
                probe_broker_address,
                work_id,
                out_lines,
                err_lines,
            )

        # Start tmux session as vscode.  Claude Code refuses
        # --dangerously-skip-permissions when EUID==0 ("cannot be used with
        # root/sudo privileges for security reasons"), so the agent must
        # run as the unprivileged vscode user — and that requires the tmux
        # server itself to be vscode-owned, because tmux refuses
        # cross-user attach (any subsequent send-keys / capture-pane /
        # has-session / attach-session call against this session must
        # therefore also run as vscode).
        self._driver.exec_run(
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
            if dsn and not self._driver.messaging_db_reachable(
                dsn, container=container
            ):
                reason = (
                    f"messaging readiness failure: messaging database at "
                    f"{SHOPMSG_DSN_ENV}={dsn} is not reachable; "
                    f"startup prompt NOT injected"
                )
                err_lines.append(reason + "\n")
                self._write_launch_diagnostic(
                    bc_name, CAUSE_MARKER_MESSAGING_DB, reason, err_lines
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
            #
            # lead-cs7k: the probe targets ``probe_broker_address`` (derived
            # from the resolved product slug, decoupled from the runtime proxy)
            # and runs from INSIDE the launched container's network context
            # (``container=container``) so its reachability matches the
            # container's, not the launcher host's.
            if not self._driver.agent_vault_reachable(
                probe_broker_address, container=container
            ):
                reason = (
                    f"agent-vault readiness failure: agent-vault broker at "
                    f"{probe_broker_address} is not reachable; "
                    f"startup prompt NOT injected"
                )
                err_lines.append(reason + "\n")
                self._write_launch_diagnostic(
                    bc_name, CAUSE_MARKER_AGENT_VAULT, reason, err_lines
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
            # Step 2/3: bounded readiness wait that resolves the workspace-trust
            # gate by polling for EITHER of two markers (lead-gw9v / lead-c713),
            # integrated with — and feeding into — the step-4 input-ready loop:
            #
            #   * CLAUDE_READY_MARKER ("Accessing workspace:") — the PRE-trust
            #     banner that appears BEFORE trust is accepted.  When it is
            #     observed first, the trust prompt is live: accept it with a
            #     bare Enter (step 3) and fall through to the step-4 input-ready
            #     wait.  This is the pre-trust path and it is UNCHANGED.
            #
            #   * CLAUDE_INPUT_READY_MARKER ("bypass permissions on") — the
            #     POST-trust input-ready marker.  bc-base bakes
            #     `bypassPermissionsModeAccepted`, so claude can SELF-ADVANCE
            #     past the workspace-trust prompt straight to input-ready; the
            #     transient "Accessing workspace:" banner is then never caught
            #     by polling.  When the pane is ALREADY at input-ready, treat
            #     claude as UP: SKIP the trust-accept Enter (there is no trust
            #     prompt to accept) and proceed directly to inject — do NOT
            #     hard-require the transient banner and do NOT abort.
            #
            # The PRIOR shape hard-gated on CLAUDE_READY_MARKER and ABORTED with
            # an "agent-startup failure" the instant the transient banner was
            # not caught — which dropped every self-advancing unattended launch
            # even though claude was healthy and sitting at input-ready.  The
            # loop below removes that hard gate while keeping the pre-trust path
            # intact and bounding the whole wait by the readiness timeout.
            trust_accepted = False
            input_ready = False
            deadline = (
                self._monotonic() + CLAUDE_READINESS_TIMEOUT_SECONDS
            )
            while True:
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    break
                per_attempt = min(
                    READINESS_DISMISS_POLL_SECONDS, remaining
                )
                # Poll for the PRE-trust banner first so the pre-trust path
                # (banner observed → accept trust with Enter) is unchanged.
                banner = self._driver.wait_for_pane_marker(
                    container,
                    AGENT_TMUX_SESSION,
                    CLAUDE_READY_MARKER,
                    per_attempt,
                )
                if banner:
                    # Step 3: accept the workspace-trust prompt (default "Yes, I
                    # trust").  Empirically verified (2026-05-29) that
                    # --dangerously-skip-permissions does NOT, on its own,
                    # bypass workspace trust when the prompt IS presented; this
                    # Enter advances past it.  It fires ONLY on the pre-trust
                    # path — never when claude self-advanced (below).
                    self._driver.exec_run(
                        container,
                        ["tmux", "send-keys", "-t", AGENT_TMUX_SESSION, "Enter"],
                        user=AGENT_CONTAINER_USER,
                    )
                    trust_accepted = True
                    break
                # Banner not caught this attempt.  Capture the pane: if claude
                # has SELF-ADVANCED past the trust prompt straight to the
                # input-ready marker, treat it as up and SKIP the trust-accept
                # Enter entirely.
                pane = self._driver.capture_pane(
                    container, AGENT_TMUX_SESSION
                )
                if CLAUDE_INPUT_READY_MARKER in pane:
                    input_ready = True
                    out_lines.append(
                        "Agent self-advanced past the workspace-trust prompt "
                        "to the input-ready marker "
                        f"{CLAUDE_INPUT_READY_MARKER!r}; treating the agent as "
                        "up and skipping the trust-accept Enter (lead-gw9v)\n"
                    )
                    break
                # Neither marker yet.  Keep polling until the deadline.
                if self._monotonic() >= deadline:
                    break
            if not trust_accepted and not input_ready:
                # Neither the PRE-trust banner nor the self-advanced input-ready
                # marker was reached within the readiness timeout: claude (or
                # its tmux session) never came up.  Warn (host-discoverable)
                # and abort WITHOUT injecting.
                reason = (
                    f"agent-startup failure: Claude Code did not become ready "
                    f"within {CLAUDE_READINESS_TIMEOUT_SECONDS:.0f}s — the agent "
                    f"never reached input-ready: neither the workspace-trust "
                    f"banner {CLAUDE_READY_MARKER!r} nor the input-ready marker "
                    f"{CLAUDE_INPUT_READY_MARKER!r} was observed within the "
                    f"readiness timeout (claude or its tmux session never "
                    f"started); startup prompt NOT injected"
                )
                err_lines.append("warning: " + reason + "\n")
                self._write_launch_diagnostic(
                    bc_name, CAUSE_MARKER_AGENT_STARTUP, reason, err_lines
                )
                return CommandResult(
                    exit_code=1,
                    stdout="".join(out_lines),
                    stderr="".join(err_lines),
                )
            # Step 4: wait for the POST-trust input-ready marker, with
            # bounded auto-dismissal of unexpected interactive prompts
            # (lead-cw7m / lead-c713).  SKIPPED when claude already
            # self-advanced to input-ready above (lead-gw9v).
            #
            # CLAUDE_INPUT_READY_MARKER is "bypass permissions on" — only
            # present once the trust prompt has cleared AND
            # --dangerously-skip-permissions is active, which is the exact
            # state in which the user prompt can be safely injected.
            #
            # The new bc-base Claude Code image (c50b3b) can render an EARLIER
            # interactive prompt (e.g. "Try the new fullscreen renderer?")
            # that BLOCKS reaching input-ready.  A single narrow wait would
            # time out at 60s and never inject.  Instead, run a BOUNDED
            # scan-dismiss loop: each iteration waits for the input-ready
            # marker for a short per-attempt budget; if it does not appear,
            # capture the pane and, if it presents an Esc-dismissable prompt
            # that is NOT the workspace-trust prompt, send ONLY Esc (decline —
            # NEVER Enter / '1', so the renderer is NOT enabled), emit a
            # host-discoverable WARNING NAMING the prompt, and continue.  The
            # TOTAL elapsed time is bounded by CLAUDE_READINESS_TIMEOUT_SECONDS;
            # on timeout WITHOUT input-ready the loop STOPS attempting
            # dismissals (no infinite loop), warns, and proceeds WITHOUT
            # injecting.
            #
            # lead-gw9v: when claude SELF-ADVANCED past the trust prompt (above),
            # input_ready is already True and the agent is already at the
            # input-ready marker — there is nothing left to wait for or dismiss,
            # so this whole loop is SKIPPED and we proceed straight to inject.
            if not input_ready:
                deadline = (
                    self._monotonic() + CLAUDE_READINESS_TIMEOUT_SECONDS
                )
                while True:
                    remaining = deadline - self._monotonic()
                    if remaining <= 0:
                        break
                    per_attempt = min(
                        READINESS_DISMISS_POLL_SECONDS, remaining
                    )
                    input_ready = self._driver.wait_for_pane_marker(
                        container,
                        AGENT_TMUX_SESSION,
                        CLAUDE_INPUT_READY_MARKER,
                        per_attempt,
                    )
                    if input_ready:
                        break
                    # Input-ready not yet observed within this attempt.  Capture
                    # the pane and look for a blocking readiness-wait prompt to
                    # auto-dismiss with Esc.
                    pane = self._driver.capture_pane(
                        container, AGENT_TMUX_SESSION
                    )
                    prompt_name = _readiness_wait_blocking_prompt(pane)
                    if prompt_name is None:
                        # No Esc-dismissable prompt is blocking; nothing more to
                        # do this iteration — keep polling until the deadline.
                        if self._monotonic() >= deadline:
                            break
                        continue
                    # Send a DISCRETE send-keys carrying ONLY the Escape key
                    # payload — NOT Enter, and NOT '1'.  This declines the
                    # prompt with its own non-committal default (e.g. does NOT
                    # enable the fullscreen renderer) and lets the loop proceed.
                    self._driver.exec_run(
                        container,
                        ["tmux", "send-keys", "-t", AGENT_TMUX_SESSION,
                         ESCAPE_KEY_NAME],
                        user=AGENT_CONTAINER_USER,
                    )
                    err_lines.append(
                        "warning: an unexpected interactive prompt was "
                        "auto-dismissed during the readiness wait (sent Escape "
                        f"to the tmux session {AGENT_TMUX_SESSION!r}, NOT Enter, "
                        "so no default was confirmed and the fullscreen "
                        f"renderer was NOT enabled); the prompt was: "
                        f"{prompt_name!r} (lead-cw7m)\n"
                    )
                    out_lines.append(
                        "Auto-dismissed an unexpected interactive prompt with "
                        "Escape during the readiness wait (lead-cw7m): "
                        f"{prompt_name!r}\n"
                    )
                    # Continue the loop: re-wait for the input-ready marker.
                if not input_ready:
                    # BOUNDED: the scan-dismiss loop terminated at the 60s
                    # deadline rather than looping indefinitely.  Stop
                    # attempting dismissals, warn that the main input did not
                    # become ready, and proceed WITHOUT injecting.
                    reason = (
                        f"readiness failure: Claude Code workspace-trust prompt "
                        f"did not clear / main input did not become ready "
                        f"within {CLAUDE_READINESS_TIMEOUT_SECONDS:.0f} seconds "
                        f"(marker {CLAUDE_INPUT_READY_MARKER!r} not seen; the "
                        f"readiness barrier never reported both supporting "
                        f"servers ready); startup prompt NOT injected"
                    )
                    err_lines.append("warning: " + reason + "\n")
                    self._write_launch_diagnostic(
                        bc_name, CAUSE_MARKER_READINESS, reason, err_lines
                    )
                    return CommandResult(
                        exit_code=1,
                        stdout="".join(out_lines),
                        stderr="".join(err_lines),
                    )
            # Step 4b: blocking interactive option-screen handling (lead-q3uy).
            #
            # After the input-ready marker but BEFORE the prompt is submitted,
            # the agent runtime can present a blocking interactive option
            # screen that absorbs keystrokes.  Capture the pane ONCE and
            # classify it:
            #   * recognized blocking option screen WITH an escape affordance →
            #     send a DISCRETE send-keys carrying ONLY the Escape key (never
            #     Enter — and never an Enter to "select a default"), capture the
            #     dismissed screen's content, log it as a host-discoverable
            #     WARNING, then fall through to submit the prompt directly;
            #   * recognized blocking option screen with NO escape affordance →
            #     do NOT send Enter / do NOT auto-confirm a default; surface a
            #     WARNING naming the un-escapable screen and do NOT submit the
            #     prompt (which the screen would swallow);
            #   * no blocking option screen → proceed to submit as normal.
            pane = self._driver.capture_pane(container, AGENT_TMUX_SESSION)
            if OPTION_SCREEN_MARKER in pane:
                if ESCAPE_AFFORDANCE_MARKER in pane:
                    # Capture the rendered content BEFORE dismissing, so the
                    # WARNING records exactly what was auto-dismissed.
                    dismissed_content = pane
                    # Discrete send-keys carrying ONLY the Escape key payload —
                    # NOT Enter, and NOT a text+Enter pair.  This dismisses the
                    # escape-able screen without selecting any default option.
                    self._driver.exec_run(
                        container,
                        ["tmux", "send-keys", "-t", AGENT_TMUX_SESSION,
                         ESCAPE_KEY_NAME],
                        user=AGENT_CONTAINER_USER,
                    )
                    err_lines.append(
                        "warning: an interactive option screen was "
                        "auto-dismissed during engage (sent Escape to the "
                        f"tmux session {AGENT_TMUX_SESSION!r}); rendered "
                        "content of the dismissed screen follows so a human "
                        "can review what was auto-dismissed (lead-q3uy):\n"
                        f"{dismissed_content}\n"
                    )
                    out_lines.append(
                        "Auto-dismissed a blocking interactive option screen "
                        "with Escape during engage (lead-q3uy)\n"
                    )
                else:
                    # No escape affordance: refuse to auto-confirm.  Pressing
                    # Enter here would blindly select whatever option is
                    # highlighted, so send NOTHING and do NOT submit the prompt.
                    err_lines.append(
                        "warning: engage encountered a blocking interactive "
                        "screen with NO escape/dismiss affordance; the launcher "
                        "did NOT send Enter and did NOT auto-confirm a default; "
                        "the startup prompt was NOT submitted.  Un-escapable "
                        "screen content follows so a human can review it from "
                        f"the host (lead-q3uy):\n{pane}\n"
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

        return CommandResult(
            exit_code=0, stdout="".join(out_lines), stderr="".join(err_lines)
        )

    # ------------------------------------------------------------------
    # start-agent — recovery: drive agent-start against an already-cloned
    # healthy container without re-cloning (lead-k4k7)
    # ------------------------------------------------------------------

    def start_agent(
        self,
        bc_name: str,
        startup_prompt: str | None = None,
        shopmsg_dsn: str | None = None,
        agent_vault_broker: str | None = None,
        manifest_path: Path | None = None,
    ) -> CommandResult:
        """Recovery subcommand: drive the agent-start sequence against an
        ALREADY-cloned, healthy container that has no agent — WITHOUT
        re-cloning.

        lead-k4k7.  Makes first-class the manual recovery the lead performed
        when a transient skill-refresh failure stranded a fully-cloned
        "Up (healthy)" container with no agent session.  It runs the SAME
        agent-start sequence ``launch`` uses (``_start_agent_session``): tmux
        new-session as vscode, the messaging-DB + agent-vault readiness
        barriers, ``agent-vault run -- claude``, the readiness-marker waits,
        and the prompt injection — but NO clone, NO beads provisioning, and NO
        skill-refresh.  It is idempotent / safe to re-run on a container
        stranded with a clone but no agent.

        Resolution of the readiness-probe inputs mirrors ``launch``: the DSN
        comes from ``shopmsg_dsn`` (falling back to the container's recorded
        DSN, then the ``SHOPMSG_DSN`` process env), and the probe broker is
        derived from the resolved product slug (an explicit broker still wins).
        """
        container = _container_name(bc_name)

        if not self._driver.is_running(container):
            return CommandResult(
                exit_code=1,
                stderr=(
                    f"{container} is not running; start-agent recovers an "
                    "already-cloned, healthy container that has no agent — "
                    "run `bc-container launch` first to create it\n"
                ),
            )

        # lead-pixf (aeebb281): detect an ALREADY-live agent and NO-OP.
        #
        # start-agent's purpose is to RECOVER a container stranded with no
        # agent.  When the "agent" tmux session ALREADY holds a live claude
        # at the input-ready marker, there is nothing to recover: re-running
        # the agent-start sequence would (a) start a SECOND
        # `agent-vault run -- claude` in the same session and (b) block on
        # the readiness-marker probe until it times out, since a session
        # already past input-ready never re-presents the trust banner.  So
        # short-circuit BEFORE the agent-start sequence: report the agent is
        # already live and online, exit zero, and DO NOT touch the session
        # (no readiness probe, no second claude).
        if self._agent_online(container):
            return CommandResult(
                exit_code=0,
                stdout=(
                    f"{container} already has a live agent and is online; "
                    "start-agent is a no-op (no readiness probe run, no "
                    "second claude agent started)\n"
                ),
            )

        out_lines: list[str] = []
        err_lines: list[str] = []

        # Resolve the messaging DSN for the readiness barrier: explicit arg >
        # SHOPMSG_DSN process env.  start-agent recovers a container that was
        # launched with its DSN already baked into the container env, so the
        # readiness barrier is best driven from the SAME source the operator
        # used at launch (an explicit --shopmsg-dsn, or the SHOPMSG_DSN env).
        dsn = shopmsg_dsn or os.environ.get(SHOPMSG_DSN_ENV)

        # Resolve the probe broker address the same way launch does: an
        # explicit broker wins, else derive from the resolved product slug
        # (manifest product > SHOPMSG_SYSTEM_SLUG env > default).
        explicit_broker = (
            agent_vault_broker
            or os.environ.get("BCLAUNCHER_AGENT_VAULT_BROKER")
        )
        manifest_product: str | None = None
        try:
            manifest_product = _read_product_from_manifest(
                manifest_path or Path("bc-manifest.yaml")
            )
        except ManifestProductTypeError:
            manifest_product = None
        if env_system_slug := os.environ.get(SHOPMSG_SYSTEM_SLUG_ENV):
            resolved_system_slug = env_system_slug
        elif manifest_product:
            resolved_system_slug = manifest_product
        else:
            resolved_system_slug = DEFAULT_SYSTEM_SLUG
        probe_broker_address = resolve_probe_broker_address(
            explicit_broker, resolved_system_slug
        )

        out_lines.append(
            f"Recovering agent in already-cloned container {container} "
            "(no re-clone)\n"
        )
        return self._start_agent_session(
            bc_name,
            container,
            startup_prompt,
            dsn,
            probe_broker_address,
            out_lines,
            err_lines,
        )

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
        """Report running state of the BC container and its tmux session.

        lead-wdvx (Bug 2): ``status`` is docker-dependent — its first act
        probes docker for the container's running state.  When that probe
        fails because the docker socket is unreachable (daemon down, OR a
        CONFIG fault: socket mounted-but-permission-denied, or not mounted),
        the driver raises ``DockerSocketUnreachableError``.  We surface that
        as a NON-ZERO exit with a stderr line NAMING the cause, distinct from
        the legitimate "container_state: stopped" an absent container reports
        at exit 0 — so a docker config fault is never masked as a (false)
        absent/stopped result.
        """
        container = _container_name(bc_name)
        try:
            is_running = self._driver.is_running(container)
        except DockerSocketUnreachableError as exc:
            detail = str(exc).strip()
            stderr = (
                "bc-container status: the Docker socket could not be reached "
                "(the Docker daemon is unreachable); cannot determine "
                "container state"
            )
            if detail:
                stderr += f": {detail}"
            return CommandResult(exit_code=1, stdout="", stderr=stderr + "\n")

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

        # Agent presence (lead-pixf / f2ddd6c7).  The tmux "agent" session
        # being merely present ("active") does NOT by itself mean an agent
        # is actually doing work: an empty session left at a bash prompt is
        # "active" but offline.  An agent is ONLINE only when the "agent"
        # tmux session holds a LIVE claude process whose `shop-msg watch`
        # inbox watcher is armed — that is the state in which the BC is
        # actually reachable for dispatched work.  Anything short of that
        # (no session, a session with no live claude, or a claude whose
        # watcher is not armed) is reported as "offline".
        agent_presence = (
            "online" if self._agent_online(container) else "offline"
        )

        return CommandResult(
            exit_code=0,
            stdout=(
                f"bc_name: {bc_name}\n"
                f"container: {container}\n"
                f"container_state: running\n"
                f"tmux_session: {tmux_state}\n"
                f"agent_presence: {agent_presence}\n"
            ),
        )

    # ------------------------------------------------------------------
    # agent presence (lead-pixf) — shared by status + start-agent
    # ------------------------------------------------------------------

    def _agent_online(self, container: str) -> bool:
        """Return True when the container's "agent" tmux session holds a LIVE
        claude process whose ``shop-msg watch`` inbox watcher is armed.

        lead-pixf.  This is the agent-presence determinant for the ``status``
        report (f2ddd6c7) and the no-op short-circuit for ``start-agent``
        (aeebb281).  It is delegated to the driver so the real driver can
        probe the live in-container process table / watcher state while the
        fake can model a live-agent container directly.  When the driver does
        not expose the probe (older driver), presence resolves to False so the
        command degrades to "offline" rather than crashing.
        """
        probe = getattr(self._driver, "agent_online", None)
        return bool(probe(container)) if callable(probe) else False

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

    # lead-q5k7 — derive the shop-type for `shop-templates update
    # --shop-type <bc|lead>` from the cloned shop's own canonical marker
    # file `.claude/shop/type.md` (contents "bc" or "lead").  The update
    # MUST use the type the shop was originally bootstrapped with, and the
    # cloned repo carries that marker, so this is the authoritative source.
    # Defaults to "bc" when the marker is absent/unreadable/unrecognised so
    # the refresh still runs with the dominant shop type rather than
    # crashing the launch.
    def _read_shop_type(self, container: str) -> str:
        result = self._driver.exec_run(
            container,
            ["cat", f"{CONTAINER_WORKSPACE}/.claude/shop/type.md"],
        )
        if result.returncode == 0:
            value = (result.stdout or "").strip().lower()
            if value in ("bc", "lead"):
                return value
        return "bc"

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
        """List all known BC containers with their states.

        lead-pixf (010e776c): when the Docker socket is unreachable, the
        driver raises ``DockerSocketUnreachableError`` rather than returning
        an empty list.  We surface that as a NON-ZERO exit with a stderr
        line naming the docker-socket unreachability and emit NOTHING on
        stdout — in particular NOT "No BC containers found", which would
        mask an infrastructure outage as a (false) empty inventory.
        """
        try:
            infos = self._driver.list_bc_containers()
        except DockerSocketUnreachableError as exc:
            detail = str(exc).strip()
            stderr = (
                "bc-container list: the Docker socket could not be reached "
                "(the Docker daemon is unreachable); cannot enumerate BC "
                "containers"
            )
            if detail:
                stderr += f": {detail}"
            return CommandResult(exit_code=1, stdout="", stderr=stderr + "\n")
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
