"""LaunchMixin for BcContainerController (controller.py decomposition, Phase 2).

Split from the former monolithic BcContainerController. Combined back into
the single public class in bc_launcher.controller.core; methods call each
other through ``self`` exactly as before.
"""
from __future__ import annotations
import os
from pathlib import Path

from bc_launcher.agent_vault import (
    AGENT_VAULT_ADDR_ENV,
    AGENT_VAULT_BROKER_ENV,
    AGENT_VAULT_PROXY_ENV,
    AGENT_VAULT_TOKEN_ENV,
    AGENT_VAULT_VAULT_ENV,
    DEFAULT_AGENT_VAULT_BROKER,
    _build_runtime_proxy_url,
)
from bc_launcher.constants import (
    AGENT_CONTAINER_USER,
    BC_IMAGE,
    BC_IMAGE_ENV,
    CONTAINER_WORKSPACE,
    DOCKER_SOCKET_PATH,
    SHOPMSG_DSN_ENV,
)
from bc_launcher.controller._result import (
    CommandResult,
)
from bc_launcher.fabro import (
    ANTHROPIC_OAUTH_SHIM_BIN,
    FABRO_ANTHROPIC_ADAPTER,
    FABRO_ANTHROPIC_BASE_URL,
    FABRO_DEF_CONTAINER_DIR,
    FABRO_DEF_FILES,
    FABRO_SERVER_INSTALL_GH_TOKEN,
    FABRO_SETTINGS_CONTAINER_PATH,
    FABRO_SHIM_HOST,
    FABRO_SHIM_PORT,
    FABRO_WORKFLOW_TOML_CONTAINER_PATH,
    LAUNCH_PATH_FABRO,
    LAUNCH_PATH_TMUX,
    _fabro_exec_env,
    _fabro_settings_install_script,
    _fabro_shim_start_script,
)
from bc_launcher.manifest import (
    ManifestProductTypeError,
    _read_product_from_manifest,
    _resolve_manifest_remote,
)
from bc_launcher.naming import (
    _container_name,
    _slugify,
)
from bc_launcher.networking import (
    DEFAULT_SYSTEM_SLUG,
    SHOPMSG_SYSTEM_SLUG_ENV,
    resolve_probe_broker_address,
)


class LaunchMixin:

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

        # --- Runtime GH_TOKEN placeholder (lead-za30) ---
        # `gh` insists on a NON-EMPTY token in its own env before it will emit
        # any request: a bare `gh api user` from inside the container otherwise
        # pre-flight-refuses ("gh auth login" / "populate GH_TOKEN") BEFORE its
        # request ever reaches the agent-vault MITM proxy above — even though
        # the broker IS wired and WOULD substitute the real GitHub credential on
        # the wire. git already rides agent-vault transparently off the runtime
        # HTTPS_PROXY; gh did not, purely for this reason. Carry the existing
        # dummy PLACEHOLDER constant into the persistent runtime env so gh
        # becomes symmetric to git — the placeholder grants NO access (it is a
        # sentinel, never a real token; the broker substitutes the real
        # credential on the outbound request). Extends the already-blessed
        # exec-scope precedent (GAP-A / lead-3mez, beads-provisioning exec env)
        # to the persistent runtime env the agent inherits.
        env["GH_TOKEN"] = FABRO_SERVER_INSTALL_GH_TOKEN

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

        mounts, docker_socket_group_add = self._build_launch_mounts(
            workspace_mount, mount_docker_socket, env
        )

        launch_image = self._resolve_launch_image(image)

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
            provision_result = self._provision_cloned_workspace(
                container,
                bc_name,
                repo_url,
                resolved_av_addr,
                resolved_av_token,
                resolved_av_vault,
                out_lines,
                err_lines,
            )
            if provision_result is not None:
                return provision_result

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
        # lead-a3kg / uyj1 completion (folds lead-bq2z): the def is DELIVERED BY
        # THE SHOP-TEMPLATES POUR on the CLONE path (lead-ona9, scenario
        # 7700eea079ffe1d8 — the pour emits "/workspace/.fabro/" parallel to
        # ".claude/skills/") and is ALREADY COMMITTED in the mounted tree on the
        # WORKSPACE-MOUNT path.  The baked-asset bundle is retired, so there is
        # NO stream-placement step here — only the fabro-specific wiring
        # (workflow.toml in-container rewrite + shim + settings) runs on the
        # POURED def.  It runs BEFORE the engage in `_start_agent_session`, so
        # the fabro server + run find the def + settings.
        if launch_path == LAUNCH_PATH_FABRO:
            self._place_fabro_def_and_wiring(
                bc_name,
                container,
                work_id,
                out_lines,
                err_lines,
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
