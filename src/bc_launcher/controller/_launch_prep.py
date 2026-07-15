"""LaunchPrepMixin for BcContainerController: run-spec helpers (mounts, image)
and fabro def/wiring placement, extracted from launch. Combined via self in
core.py.
"""
from __future__ import annotations
import base64
import os

from bc_launcher.constants import (
    AGENT_CONTAINER_USER,
    BC_IMAGE,
    BC_IMAGE_ENV,
    CONTAINER_WORKSPACE,
    DOCKER_SOCKET_PATH,
    SHOPMSG_DSN_ENV,
)
from bc_launcher.fabro import (
    ANTHROPIC_OAUTH_SHIM_BIN,
    OPENROUTER_SHIM_BIN,
    FABRO_ANTHROPIC_ADAPTER,
    FABRO_ANTHROPIC_BASE_URL,
    FABRO_DEF_CONTAINER_DIR,
    FABRO_DISPATCHER_TOML_CONTAINER_PATH,
    FABRO_OPENROUTER_SHIM_PORT,
    FABRO_SETTINGS_CONTAINER_PATH,
    FABRO_SHIM_HOST,
    FABRO_SHIM_PORT,
    FABRO_WORKFLOW_TOML_CONTAINER_PATH,
    LLM_PROVIDER_ANTHROPIC,
    LLM_PROVIDER_OPENROUTER,
    _fabro_exec_env,
    _fabro_settings_install_script,
    _fabro_shim_start_script,
    _openrouter_shim_start_script,
    _fabro_workflow_toml_read_script,
    _fabro_workflow_toml_rewrite,
    _fabro_workflow_toml_writeback_script,
    resolve_llm_provider,
)


class LaunchPrepMixin:


    # ------------------------------------------------------------------
    # FABRO def + wiring placement (lead-h2bj / lead-vwib; hoisted out of the
    # clone-only guard by lead-ze4w BUG#1 so it runs on the workspace-mount
    # path too)
    # ------------------------------------------------------------------

    def _build_launch_mounts(
        self,
        workspace_mount: str | None,
        mount_docker_socket: bool,
        env: dict[str, str],
    ) -> tuple[list[tuple[str, str, str, bool]], list[str]]:
        """Assemble the docker run bind-mounts + docker-socket group-add
        (extracted verbatim from ``launch``)."""
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
        return mounts, docker_socket_group_add

    def _resolve_launch_image(self, image: str | None) -> str:
        """Resolve the launch image (flag > env > default) and, when a
        registry driver is present, pin+pull the current digest (extracted
        verbatim from ``launch``)."""
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
        return launch_image

    def _rewrite_poured_workflow_toml(
        self,
        bc_name: str,
        container: str,
        work_id: str | None,
        out_lines: list[str],
        err_lines: list[str],
        toml_path: str = FABRO_WORKFLOW_TOML_CONTAINER_PATH,
    ) -> None:
        """READ the poured ``toml_path`` in-container, rewrite its BC_NAME /
        WORK_ID to the launch's ACTUAL identity on the host, and WRITE it back
        over the poured path (lead-a3kg / uyj1 completion).

        Parameterized by ``toml_path`` (lead-e5jx) so it operates on ANY poured
        run-config toml, not only workflow.toml: the caller invokes it for BOTH
        ``/workspace/.fabro/workflow.toml`` (the ADR-051 child def's run config)
        AND ``/workspace/.fabro/dispatcher.toml`` (the ADR-058 reactive engage's
        entrypoint).  Both ship the bundle-default identity in their
        [run.environment.env] / [run.inputs] tables, and the shared
        ``_fabro_workflow_toml_rewrite`` substitutes every BC_NAME/WORK_ID line
        in either table regardless of which file it came from.

        This replaces the retired baked-host-asset read (lead-ze4w BUG#2's
        original mechanism), which FileNotFoundErrors in an installed wheel once
        the lead-ona9 package-data removal ships.  A read/rewrite/write-back
        failure is a boot-convenience warn-and-continue, never a fatal abort.
        """
        read_result = self._driver.exec_run(
            container,
            ["/bin/sh", "-c", _fabro_workflow_toml_read_script(toml_path)],
            user=AGENT_CONTAINER_USER,
        )
        if read_result.returncode != 0:
            err_lines.append(
                "warning: fabro run-config toml read failed (exit "
                f"{read_result.returncode}): "
                f"{(read_result.stderr or read_result.stdout).strip()}"
                f"; {toml_path} could not be read to "
                "rewrite BC_NAME/WORK_ID but the agent will still be started "
                "(lead-a3kg)\n"
            )
            return

        try:
            source = base64.b64decode(read_result.stdout).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            err_lines.append(
                "warning: fabro run-config toml read returned undecodable bytes "
                f"({exc}); {toml_path} may carry the "
                "bundle defaults but the agent will still be started "
                "(lead-a3kg)\n"
            )
            return

        rewritten = _fabro_workflow_toml_rewrite(source, bc_name, work_id or "")
        workflow_result = self._driver.exec_run(
            container,
            ["/bin/sh", "-c",
             _fabro_workflow_toml_writeback_script(rewritten, toml_path)],
            user=AGENT_CONTAINER_USER,
        )
        if workflow_result.returncode != 0:
            err_lines.append(
                "warning: fabro run-config toml BC_NAME/WORK_ID rewrite failed "
                f"(exit {workflow_result.returncode}): "
                f"{(workflow_result.stderr or workflow_result.stdout).strip()}"
                f"; {toml_path} may carry the bundle "
                "defaults but the agent will still be started (lead-ze4w)\n"
            )
        else:
            out_lines.append(
                "Rewrote the poured "
                f"{toml_path} [run.environment.env] / "
                f"[run.inputs] BC_NAME={bc_name} WORK_ID={work_id or ''} "
                "(lead-ze4w BUG#2, lead-a3kg in-container; lead-e5jx both "
                "workflow.toml + dispatcher.toml)\n"
            )

    def _place_fabro_def_and_wiring(
        self,
        bc_name: str,
        container: str,
        work_id: str | None,
        out_lines: list[str],
        err_lines: list[str],
        active_provider: str | None = None,
    ) -> None:
        """Place the fabro-orchestrator wiring (workflow.toml identity rewrite
        + shim start + effective settings) onto the POURED def in the launched
        container.

        lead-ze4w BUG#1: this runs on BOTH the clone and the workspace-mount
        launch paths (the caller invokes it OUTSIDE the clone-only guard), so a
        `--workspace-mount --orchestrator fabro` launch actually has
        /workspace/.fabro before `_start_agent_session` runs `fabro engage`.
        It is only invoked on the fabro path, so the tmux default is unchanged.

        lead-a3kg / uyj1 completion (folds lead-bq2z): under N4 (lead-ona9) the
        self-contained fabro loop def is DELIVERED by the shop-templates POUR
        (clone path) or is ALREADY COMMITTED in the mounted tree
        (workspace-mount path) at /workspace/.fabro/ — the baked
        `src/bc_launcher/assets/fabro-def/` bundle is RETIRED from the wheel and
        image, so there is NO baked-asset placement step here anymore.  The
        wiring operates on the POURED def actually present in the container.

        Steps (each a boot-convenience warn-and-continue on failure — never a
        fatal abort that strands a healthy container with no agent):

          (1) REWRITE the POURED workflow.toml's BC_NAME / WORK_ID to the
              launch's ACTUAL bc_name / work_id, IN THE CONTAINER (lead-ze4w
              BUG#2, lead-a3kg): READ the poured /workspace/.fabro/workflow.toml
              via exec, rewrite BC_NAME/WORK_ID on the host, WRITE it back —
              never the retired baked host asset.
          (2) START the baked so2h shim as a background listener, with
              SSL_CERT_FILE pinned (lead-ze4w BUG#3).
          (3) WRITE fabro's effective workflow-level settings.toml pointing the
              anthropic provider at the shim; NO credential (ADR-049 D1/D2).
          (4) chown the placed .fabro/ tree to the agent user so the placed def
              + writes are agent-owned (on the workspace-mount path this touches
              ONLY the launcher-created .fabro/ subtree, never the mounted
              tree's committed .beads / .claude).
        """
        # (1) Rewrite the POURED workflow.toml's BC_NAME / WORK_ID to the
        #     launch's ACTUAL identity (lead-ze4w BUG#2, lead-a3kg).  Under N4
        #     the def is delivered by the pour / committed in the mount at
        #     /workspace/.fabro/, so the rewrite READS the poured file
        #     IN-CONTAINER (never the retired baked host asset), rewrites its
        #     [run.inputs] + [run.environment.env] BC_NAME/WORK_ID on the host,
        #     and writes the corrected bytes back over the poured path.  The
        #     native script= nodes read $BC_NAME / $WORK_ID from the
        #     [run.environment.env] overlay (which `fabro run -I` does NOT
        #     override), so this rewrite is what carries the launch's real
        #     identity into every native node.
        #     lead-e5jx: rewrite BOTH poured run-config tomls.  The reactive
        #     engage is `fabro run dispatcher.toml`, and the dispatcher's native
        #     watch/dispatch nodes read $BC_NAME from dispatcher.toml's
        #     [run.environment.env] overlay — so dispatcher.toml MUST be
        #     rewritten too, or the reactive watcher runs
        #     `dispatch_acp_agent.py --bc fabro-throwaway` / `shop-msg watch
        #     --bc fabro-throwaway` (the bundle default) instead of the launch
        #     BC.  workflow.toml carries the ADR-051 child def's identity; both
        #     go through the same in-container read/rewrite/write-back.
        self._rewrite_poured_workflow_toml(
            bc_name, container, work_id, out_lines, err_lines,
            toml_path=FABRO_WORKFLOW_TOML_CONTAINER_PATH,
        )
        self._rewrite_poured_workflow_toml(
            bc_name, container, work_id, out_lines, err_lines,
            toml_path=FABRO_DISPATCHER_TOML_CONTAINER_PATH,
        )

        # (2)/(3) ANTHROPIC-OAUTH-SHIM PATH — engaged ONLY on the anthropic
        #     active provider (lead-ifye3.2 behavior 2).  The shim start and the
        #     anthropic-pointing effective settings ARE the anthropic-oauth-shim
        #     wiring; branching them on the resolved active provider means an
        #     explicit --llm-provider / BCLAUNCHER_LLM_PROVIDER override
        #     (openrouter) WINS over the anthropic default and BYPASSES the shim
        #     entirely — NO shim listener is started and NO anthropic-pointing
        #     settings are written on the openrouter path.  (The openrouter
        #     credential + provider-keyed settings are behaviors 3-4.)  The
        #     active provider is resolved here (idempotent for an already-resolved
        #     value) so this method is correct whether the caller passes the
        #     resolved provider or leaves it to the anthropic default.
        resolved_active_provider = resolve_llm_provider(
            active_provider, env=os.environ
        )
        if resolved_active_provider == LLM_PROVIDER_ANTHROPIC:
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
        elif resolved_active_provider == LLM_PROVIDER_OPENROUTER:
            # OPENROUTER-SHIM PATH (lead-ifye3.5 behavior 1).  The openrouter
            # override's [llm.providers.openai] base_url points at the LOCAL
            # openrouter-shim loopback (registered AT THE SERVER by the engage),
            # so here the launcher STARTS the openrouter-shim as an UNSANDBOXED,
            # container-level background listener — the SAME launch-lifecycle
            # shape the anthropic-oauth-shim uses, but on the openrouter-shim's
            # OWN distinct loopback port so both shims coexist.  The direct-to-
            # OpenRouter base_url could never complete a dispatch from inside
            # fabro's sandbox (env cleared/filtered + no HTTPS_PROXY egress); the
            # unsandboxed shim makes the real outbound call through HTTPS_PROXY
            # where agent-vault substitutes the credential on the shim's OWN hop.
            # The Anthropic anthropic-oauth-shim is deliberately NOT started on
            # this path.
            shim_result = self._driver.exec_run(
                container,
                ["/bin/sh", "-c", _openrouter_shim_start_script()],
                user=AGENT_CONTAINER_USER,
                env=_fabro_exec_env(),
            )
            if shim_result.returncode != 0:
                err_lines.append(
                    "warning: openrouter-shim start failed (exit "
                    f"{shim_result.returncode}): "
                    f"{(shim_result.stderr or shim_result.stdout).strip()}"
                    f"; fabro's openrouter override may lack a local endpoint "
                    f"on {FABRO_SHIM_HOST}:{FABRO_OPENROUTER_SHIM_PORT} but "
                    "the agent will still be started (lead-ifye3.5)\n"
                )
            else:
                out_lines.append(
                    "Started the baked openrouter-shim "
                    f"({OPENROUTER_SHIM_BIN}) as an unsandboxed, container-level "
                    f"background listener on {FABRO_SHIM_HOST}:"
                    f"{FABRO_OPENROUTER_SHIM_PORT} — the same launch-lifecycle "
                    "shape the anthropic-oauth-shim uses; the Anthropic "
                    "anthropic-oauth-shim path is NOT engaged for this launch "
                    "(lead-ifye3.5 behavior 1)\n"
                )
        else:
            out_lines.append(
                "Active LLM provider "
                f"{resolved_active_provider!r} (launch-time override) — the "
                "Anthropic anthropic-oauth-shim path is NOT engaged for this "
                "launch: no shim listener started and no anthropic-pointing "
                "effective settings written (lead-ifye3.2 behavior 2)\n"
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
