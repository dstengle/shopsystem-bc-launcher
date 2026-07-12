"""BeadsProvisioningMixin for BcContainerController: bd bootstrap (with
absent-repo/empty-remote heals) and shop-templates skill refresh, extracted
from _provision_cloned_workspace. Combined via self in core.py.
"""
from __future__ import annotations

from bc_launcher.constants import (
    AGENT_CONTAINER_USER,
    CONTAINER_WORKSPACE,
)
from bc_launcher.tracker_provision import (
    _beads_dolt_remote_url,
    _beads_dolt_repo_slug,
    _create_absent_tracker_repo_script,
    _empty_remote_seed_script,
    _is_empty_remote_failure,
    _is_repo_not_found_failure,
    _is_schema_skew_migration_refusal,
    _schema_skew_heal_script,
    _tracker_provision_exec_env,
)


class BeadsProvisioningMixin:

    def _provision_beads_tracker(
        self,
        container: str,
        bc_name: str,
        out_lines: list[str],
        err_lines: list[str],
    ) -> None:
        """Run ``bd bootstrap`` with absent-repo (lead-7jc2) and empty-remote
        (lead-5k8c) heals; warn-and-continue on failure. Extracted from
        ``_provision_cloned_workspace`` verbatim."""
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

        # lead-915f — REMOTE-BACKED SCHEMA-SKEW HEAL.  DISTINCT from the
        # empty-remote family above: here the `<bc>-beads` remote DOES carry
        # Dolt data, just at an OLD schema (e.g. v32) BEHIND the baked bd's
        # CURRENT target (e.g. v53), so the clone SUCCEEDS and `bd bootstrap`
        # fails because bd REFUSES to auto-apply the migrations to a
        # remote-backed database (fork hazard, bd upstream #4259).  Observed
        # live at lead-4qqi ("21 schema migrations (v32 -> v53) that bd will
        # not auto-apply to a remote-backed database (#4259)"); it hits EVERY
        # BC.  In-place `bd migrate` is proven DEAD (lead-065a: hard-fails at
        # migration 0047 "table not found: wisps").  The heal REBUILDS a fresh
        # local dolt DB at the CURRENT schema from the schema-independent
        # committed `.beads/issues.jsonl` via `bd init --from-jsonl` (taking a
        # pre-heal `bd export --all` safety net first), REFUSES for a lead-role
        # beads (sole-clone invariant), and durably reseeds the remote via a
        # brokered force-push — INSTEAD of fatal-stranding the container.  The
        # reseed force-push runs through the agent-vault brokered
        # non-interactive dolt-push path (lead-tc38, @scenario_hash
        # 5351a4a8071b594f / e3a0ec19298e7ce7); until that path is wired the
        # raw push hits the credential gap and the remote stays behind, but the
        # BC is online locally and a subsequent launch re-heals.
        if boot_result.returncode != 0 and _is_schema_skew_migration_refusal(
            boot_result.stderr or boot_result.stdout or ""
        ):
            beads_remote = _beads_dolt_remote_url(bc_name)
            shop_type = self._read_shop_type(container)
            heal_result = self._driver.exec_run(
                container,
                ["bash", "-lc",
                 _schema_skew_heal_script(beads_remote, shop_type)],
                user=AGENT_CONTAINER_USER,
            )
            if heal_result.returncode == 0:
                out_lines.append(
                    "Remote-backed beads schema-skew heal (#4259): rebuilt a "
                    "fresh current-schema dolt DB from the committed "
                    ".beads/issues.jsonl via bd init --from-jsonl and reseeded "
                    f"the remote ({beads_remote}) (lead-915f)\n"
                )
                # Re-assert vscode ownership (the rebuild re-rooted .beads),
                # then treat the heal as the provisioning result — do NOT retry
                # `bd bootstrap` (it would re-clone the still-old remote and
                # re-hit the #4259 refusal); the from-JSONL rebuild already
                # brought the local DB online at the current schema.
                self._driver.exec_run(
                    container,
                    ["chown", "-R",
                     f"{AGENT_CONTAINER_USER}:{AGENT_CONTAINER_USER}",
                     CONTAINER_WORKSPACE],
                )
                boot_result = heal_result
            else:
                err_lines.append(
                    "warning: remote-backed beads schema-skew (#4259) heal "
                    f"could not complete ({beads_remote}, exit "
                    f"{heal_result.returncode}): "
                    f"{(heal_result.stderr or heal_result.stdout).strip()}; "
                    "proceeding to agent-start so the agent can self-heal "
                    "(lead-915f)\n"
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

    def _refresh_shop_templates(
        self,
        container: str,
        out_lines: list[str],
        err_lines: list[str],
    ) -> None:
        """Re-pour the shop-templates skill-group over the cloned workspace
        (lead-dlrx/lead-q5k7); warn-and-continue on failure. Extracted from
        ``_provision_cloned_workspace`` verbatim."""
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
