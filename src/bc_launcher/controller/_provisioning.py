"""ProvisioningMixin for BcContainerController (controller.py decomposition, Phase 2).

Split from the former monolithic BcContainerController. Combined back into
the single public class in bc_launcher.controller.core; methods call each
other through ``self`` exactly as before.
"""
from __future__ import annotations

from bc_launcher.agent_vault import (
    AGENT_VAULT_PROXY_ENV,
    GIT_SSL_CAINFO_ENV,
    _build_clone_proxy_url,
    _clone_ca_materialize_script,
)
from bc_launcher.constants import (
    AGENT_CONTAINER_USER,
    AGENT_VAULT_CONTAINER_CA_PATH,
    CONTAINER_WORKSPACE,
)
from bc_launcher.controller._result import (
    CommandResult,
)
from bc_launcher.tracker_provision import (
    _beads_dolt_remote_url,
    _beads_dolt_repo_slug,
    _create_absent_tracker_repo_script,
    _empty_remote_seed_script,
    _is_empty_remote_failure,
    _is_repo_not_found_failure,
    _resolve_origin_owner_writeback_script,
    _tracker_provision_exec_env,
)


class ProvisioningMixin:

    def _provision_cloned_workspace(
        self,
        container: str,
        bc_name: str,
        repo_url: str,
        resolved_av_addr: str | None,
        resolved_av_token: str | None,
        resolved_av_vault: str | None,
        out_lines: list[str],
        err_lines: list[str],
    ) -> CommandResult | None:
        """Clone the repo and provision the in-container beads tracker.

        Runs the full clone-path provisioning span (extracted verbatim from
        ``launch``): broker-CA materialization, the brokered clone, beads
        tracker bootstrap (with absent-repo / empty-remote heals), the
        shop-templates skill refresh, fabro def-bundle placement, and the
        final ownership assertion. Appends progress to ``out_lines`` /
        ``err_lines`` in place. Returns a ``CommandResult`` to signal an
        early launch failure (CA prep / clone), or ``None`` to continue.
        """
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

        self._provision_beads_tracker(container, bc_name, out_lines, err_lines)

        self._refresh_shop_templates(container, out_lines, err_lines)

        # Self-contained fabro loop def bundle delivery (lead-ona9, scenario
        # 7700eea079ffe1d8 — reworked lead-h2bj / ADR-051 delivery).  The def is
        # now delivered by the shop-templates POUR just above
        # (`_refresh_shop_templates`), which emits "/workspace/.fabro/" EXACTLY
        # as it emits ".claude/skills/" — the fabro loop def is no longer
        # streamed from a BAKED asset off the docker exec STDIN.  lead-a3kg /
        # uyj1 completion (folds lead-bq2z): the baked-asset placement helper is
        # retired entirely — the pour is the sole delivery surface on the clone
        # path and the committed tree is the surface on the workspace-mount path
        # (`src/bc_launcher/assets/fabro-def/` remains only as the def SOURCE
        # mirror).  On the fabro path the ADDITIONAL wiring (in-container
        # workflow.toml rewrite + shim + settings) runs OUTSIDE this guard via
        # _place_fabro_def_and_wiring, operating on the poured/committed
        # "/workspace/.fabro/" — see lead-ze4w BUG#1 / lead-a3kg.

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
