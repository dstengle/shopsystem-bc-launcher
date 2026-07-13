"""Beads/Dolt work-tracker provisioning shell-script builders.

Extracted verbatim from ``controller`` (Phase 1 of the controller.py
decomposition). Leaf module; re-exported by ``controller`` for import-path
compatibility. Do not import ``controller`` from here (would cycle).
"""
from __future__ import annotations

from bc_launcher.constants import CONTAINER_WORKSPACE


# lead-5k8c — the GitHub org that owns each BC's `<bc>-beads` Dolt remote
# (mirrors the BC_IMAGE org "ghcr.io/dstengle/...").  The per-BC beads remote
# is `git+https://github.com/<org>/<bc>-beads.git`; the agent-vault proxy
# injects credentials for it via HTTPS_PROXY at exec time.
BEADS_REMOTE_ORG = "dstengle"



def _beads_dolt_remote_url(bc_name: str) -> str:
    """The `git+https://` Dolt remote URL for a BC's `<bc>-beads` registry.

    lead-5k8c.  A BC named ``shopsystem-bc-launcher`` keeps its beads working
    set on the GitHub repo ``<org>/shopsystem-bc-launcher-beads.git``; the
    launcher's empty-remote provisioning seeds + pushes to this URL.
    """
    return (
        f"git+https://github.com/{BEADS_REMOTE_ORG}/{bc_name}-beads.git"
    )



def _is_empty_remote_failure(message: str) -> bool:
    """Whether a `bd bootstrap` failure was caused by an EMPTY Dolt remote.

    lead-5k8c.  An uninitialized `<bc>-beads` GitHub repo makes bootstrap's
    clone fail with "git remote has no branches: cannot push ...; initialize
    the repository with an initial branch/commit first".

    lead-ypnz / GAP D — VERSION-ROBUST.  The CURRENT bc-base dolt fails the
    clone of a freshly `gh repo create --add-readme`'d tracker (git README
    branch present, NO dolt refs) with a DIFFERENT phrasing: "clone failed;
    remote at that url contains no Dolt data".  Both phrasings describe the
    SAME condition — an existing-but-unseeded remote — which the empty-remote
    init-and-push provisioning recovers.  So this predicate matches BOTH the
    current "contains no Dolt data" text and the legacy "git remote has no
    branches" text (keeping the legacy match makes it robust across dolt
    versions).  Other bootstrap failures fall straight through to the
    warn-and-continue path.
    """
    text = message.lower()
    return (
        "git remote has no branches" in text
        or ("no branches" in text and "initialize" in text)
        or "contains no dolt data" in text
    )



def _is_schema_skew_migration_refusal(message: str) -> bool:
    """Whether a `bd bootstrap` failure was caused by bd REFUSING to auto-apply
    schema migrations to a REMOTE-BACKED Dolt DB (fork hazard, bd upstream
    #4259) — lead-915f.

    DISTINCT from the empty-remote family (`_is_empty_remote_failure`): that
    fires when the `<bc>-beads` remote carries NO Dolt data ("no branches" /
    "contains no Dolt data"), so the clone itself fails.  HERE the remote DOES
    carry Dolt data — just at an OLD schema (e.g. v32) BEHIND the baked bd's
    CURRENT target (e.g. v53) — so the clone SUCCEEDS and `bd bootstrap` fails
    because bd will not auto-apply the migrations to a remote-backed database
    (it would fork the shared Dolt history).  Observed live at lead-4qqi:
    "Bootstrap failed ... 21 schema migrations (v32 -> v53) that bd will not
    auto-apply to a remote-backed database (#4259)".

    The schema-skew heal (rebuild-from-JSONL + reseed) recovers this — and ONLY
    this — condition; the two predicates must not misfire on each other's
    precondition, so this one is guarded to NOT match the empty-remote texts.
    """
    text = message.lower()
    if _is_empty_remote_failure(message):
        return False
    if "#4259" in text:
        return True
    mentions_schema_migration = (
        "migration" in text or "migrate" in text or "schema" in text
    )
    mentions_remote_backed = "remote-backed" in text or "remote backed" in text
    refuses = (
        "will not" in text
        or "won't" in text
        or "wont" in text
        or "refus" in text
        or "not auto-apply" in text
        or "cannot auto-apply" in text
        or "can't auto-apply" in text
    )
    return mentions_schema_migration and mentions_remote_backed and refuses



def _schema_skew_heal_script(beads_remote_url: str, shop_type: str) -> str:
    """Shell to HEAL a remote-backed beads schema-skew wall (lead-915f).

    On bd's #4259 refusal to auto-apply schema migrations to a remote-backed DB
    (the remote carries Dolt data at an OLD schema behind the baked bd's CURRENT
    target), REBUILD a fresh local dolt DB at the current schema from the
    schema-independent committed `.beads/issues.jsonl` via `bd init
    --from-jsonl` — NOT an in-place `bd migrate`, which #4259 refuses and
    lead-065a proved HARD-FAILS at migration 0047 ("table not found: wisps").

    Ordering (load-bearing):
      0. IDEMPOTENT NO-OP guard — if `bd ready` already exits zero (healthy at
         the current schema, no #4259 signal), do nothing: no rebuild, no reseed
         force-push, exit zero.  Re-running the standup is safe.
      1. LEAD-ROLE REFUSAL — the reseed force-push is HISTORY-REPLACING and safe
         only when the container is the SOLE clone of its beads remote, which
         holds for a BC but NOT for the lead.  Refuse a lead-role beads (exit
         nonzero, directing a manual migrate) BEFORE any destructive step.
      2. PRE-HEAL EXPORT — take a full `bd export --all` safety-net capture to a
         backup path BEFORE any destructive step.  If the old DB is unreadable
         the export fails; proceed anyway from the committed issues.jsonl (the
         export is only a forensic net, never the rebuild's source of truth).
      3. DESTROY — remove the broken remote-backed embedded-Dolt working set.
      4. REBUILD — `bd init --from-jsonl .beads/issues.jsonl` create-freshes a
         DB at the baked bd's CURRENT schema; the committed issues.jsonl is the
         authoritative SOURCE OF TRUTH, preserving every committed issue.
      5. RESEED — durably reseed the remote via a HISTORY-REPLACING `bd dolt
         push --force` through the agent-vault brokered non-interactive
         dolt-push credential path.  Until that brokered path is wired (lead-
         tc38, the SAME create-bc seed credential gap pinned at
         @scenario_hash:5351a4a8071b594f / e3a0ec19298e7ce7) the raw push hits
         the MITM SSL / non-interactive-credential gap and fails, leaving the
         remote behind — NON-FATAL: the BC is online locally and a subsequent
         launch re-heals.
    """
    # Strip the `git+` transport prefix so raw git accepts the URL; the dolt
    # remote itself keeps the original `git+https://` scheme.
    _git_push_url = beads_remote_url.removeprefix("git+")  # noqa: F841 (parity)
    return (
        f"set -e; cd {CONTAINER_WORKSPACE}; "
        # (0) IDEMPOTENT NO-OP guard.
        "if BD_NON_INTERACTIVE=1 bd ready >/dev/null 2>&1; then "
        "echo 'schema-skew heal: bd already healthy at the current schema; "
        "no-op (no rebuild, no reseed)'; exit 0; fi; "
        # (1) LEAD-ROLE REFUSAL (before any destructive step).
        f"shop_type='{shop_type}'; "
        "if [ \"$shop_type\" = 'lead' ]; then "
        "echo 'schema-skew heal: REFUSED for a lead-role beads; the "
        "history-replacing reseed force-push is safe only for a sole-clone BC, "
        "NOT the lead (it would discard non-reconstructable Dolt history); "
        "directing a manual migrate on the lead host instead' >&2; exit 1; fi; "
        # (2) PRE-HEAL EXPORT (safety net) BEFORE any destructive step.
        "bd export --all > .beads/pre-heal-export.jsonl 2>/dev/null || "
        "echo 'schema-skew heal: pre-heal export failed (old DB unreadable); "
        "proceeding from the committed .beads/issues.jsonl source of truth' >&2; "
        # (3) DESTROY the broken remote-backed embedded-Dolt working set.
        "rm -rf .beads/embeddeddolt; "
        # (3a) STRIP sync.remote from .beads/config.yaml BEFORE the reinit so the
        #      from-jsonl rebuild does NOT hit bd's remote-history guard ("remote
        #      has Dolt history and you selected local history without
        #      --discard-remote", exit 10).  Driving that --discard-remote branch
        #      is history-REPLACING and would diverge the BC's beads remote
        #      (lead-oqaw / v0.3.67 prod defect); stripping the remote lets the
        #      LOCAL reinit succeed without --discard-remote.  RESTORED at (4a).
        "if [ -f .beads/config.yaml ]; then "
        "cp .beads/config.yaml .beads/config.yaml.heal-bak; "
        "grep -v '^sync\\.remote:' .beads/config.yaml.heal-bak "
        "> .beads/config.yaml || true; fi; "
        # (4) REBUILD a fresh CURRENT-schema DB from the committed issues.jsonl
        #     (NOT bd migrate).  --reinit-local: create-fresh LOCAL history; with
        #     sync.remote stripped there is no configured remote to conflict, so
        #     the remote-history guard never fires and no --discard-remote is used.
        "BD_NON_INTERACTIVE=1 bd init --from-jsonl .beads/issues.jsonl "
        "--reinit-local; "
        # (4a) RESTORE sync.remote so the remote stays configured for the
        #      deferred durable reseed (lead-tc38) and future launches — the
        #      strip is TEMPORARY, never a divergence.
        "if [ -f .beads/config.yaml.heal-bak ]; then "
        "mv .beads/config.yaml.heal-bak .beads/config.yaml; fi; "
        # (5) RESEED the remote durably via a history-replacing brokered
        #     force-push; NON-FATAL on the (as-yet-unwired) credential gap.
        f"bd dolt remote add origin {beads_remote_url} 2>/dev/null || true; "
        "BD_NON_INTERACTIVE=1 bd dolt push --force || "
        "echo 'schema-skew heal: reseed force-push failed on the brokered "
        "dolt-push credential path (lead-tc38); remote stays behind, a "
        "subsequent launch will re-heal' >&2; "
        "echo 'schema-skew heal: rebuilt a fresh current-schema DB from the "
        "committed issues.jsonl via bd init --from-jsonl'"
    )



def _beads_dolt_repo_slug(bc_name: str) -> str:
    """The `<owner>/<bc>-beads` GitHub slug for a BC's beads tracker repo.

    lead-7jc2.  This is the repo the launcher CREATES when standing up a new
    BC whose `<bc>-beads` tracker does not yet exist, distinct from the lead's
    own `<product>-lead-beads`.  Mirrors the `git+https://` remote URL built by
    `_beads_dolt_remote_url` but in the `gh repo create`/`gh repo view` slug
    form (no scheme, no `.git` suffix).
    """
    return f"{BEADS_REMOTE_ORG}/{bc_name}-beads"



def _is_repo_not_found_failure(message: str) -> bool:
    """Whether a `bd bootstrap` failure was caused by an ABSENT tracker repo.

    lead-7jc2.  When the `<bc>-beads` GitHub tracker repo does not exist at
    all, bootstrap's clone fails "Repository not found" — a strictly earlier
    failure than the empty-but-existing remote's "git remote has no branches".
    That condition — and ONLY that condition — is what the absent-repo
    create-then-seed provisioning recovers; other bootstrap failures fall
    through to the empty-remote seed path (lead-5k8c) or the warn-and-continue
    path.
    """
    return "repository not found" in message.lower()



def _create_absent_tracker_repo_script(beads_repo_slug: str) -> str:
    """Shell to CREATE an ABSENT `<bc>-beads` GitHub tracker repo (lead-7jc2).

    The absent-repo case observed live: bd bootstrap's clone fails "Repository
    not found" because the `<bc>-beads` GitHub repo was never created.  This
    creates it with an INITIAL BRANCH AND COMMIT (`gh repo create ...
    --add-readme` seeds an initial commit on the default branch) so it is not
    an empty branchless repo, then verifies it is now viewable.  The
    subsequent empty-remote seed step (lead-5k8c) adds the `bd dolt remote`
    and pushes the Dolt working set.  `gh` auth flows through the agent-vault
    proxy; a create that races an already-existing repo is tolerated (the
    verify tail is the load-bearing check).
    """
    return (
        "set -e; "
        f"gh repo create {beads_repo_slug} --private --add-readme || true; "
        f"gh repo view {beads_repo_slug} >/dev/null"
    )



# lead-3mez / GAP A — the absent-repo tracker-provisioning exec above runs
# `gh repo create` through the container's agent-vault proxy (HTTPS_PROXY +
# broker CA + AGENT_VAULT_*, all wired at `docker run` time).  gh will not
# even attempt the request without a non-empty token in its env: with no
# GH_TOKEN it exits non-zero ("gh auth login" / "populate GH_TOKEN") BEFORE
# the proxy can substitute the real GITHUB_TOKEN on the wire.  Empirically
# proven (David's 2026-07-07 shopsystem-knowledge standup): re-running the
# exact in-container script with GH_TOKEN=dummy created the repo.  So the exec
# only needs a non-empty PLACEHOLDER; the broker rides the wire.  Mirrors the
# FABRO_SERVER_INSTALL_GH_TOKEN idiom (ADR-049 D1: no real cred literal).
TRACKER_PROVISION_GH_TOKEN = "gh-dummy-agent-vault-rides-the-wire"



def _tracker_provision_exec_env() -> dict[str, str]:
    """The extra exec env pinned on the absent-repo `gh repo create` provisioning
    exec (lead-3mez / GAP A): a non-empty GH_TOKEN placeholder so gh
    authenticates through the already-wired agent-vault proxy instead of
    exiting non-zero for lack of a token."""
    return {"GH_TOKEN": TRACKER_PROVISION_GH_TOKEN}



def _empty_remote_seed_script(beads_remote_url: str) -> str:
    """Shell to INITIALIZE an empty `<bc>-beads` Dolt remote (lead-5k8c).

    Mirrors the heal performed live 2026-06-22: `git init -b main` a temp
    repo seeded from the git-tracked `.beads/issues.jsonl`, push an initial
    commit to the `<bc>-beads.git` GitHub repo (agent-vault proxy injects
    creds via HTTPS_PROXY), then `bd dolt remote add origin <url>` +
    `bd dolt push`, and verify `refs/dolt/data` appears in `git ls-remote`.

    lead-ktl0 / GAP E — the RAW-git operations (`git push`, `git ls-remote`)
    must target the PLAIN `https://` tracker URL, NOT the `git+https://` DOLT
    remote URL.  `git+https` is a Dolt-tooling transport convention; passed to
    raw git it errors "git: 'remote-git+https' is not a git command; fatal:
    remote helper 'git+https' aborted session" (exit 128).  Under `set -e` that
    FATAL push aborted the seed BEFORE `bd dolt push` (the step that actually
    seeds the Dolt data), stranding the tracker so the retried `bd bootstrap`
    failed "contains no Dolt data".  So: (a) the raw-git ops use the plain
    `https://` URL (strip the `git+` prefix), and (b) the git-side push is made
    NON-FATAL (`|| true`) — `create-absent` already `gh repo create
    --add-readme`'d the initial branch/commit, so the git-side push is
    redundant/optional and its failure must never abort the seed before
    `bd dolt push` runs.  Only the raw-git ops need the plain scheme; the
    `bd dolt remote add origin` keeps the `git+https://` DOLT remote URL — that
    is the correct scheme for bd's own dolt tooling.

    lead-tc38 / GAP H (ROOT, supersedes GAP G) — UNCONFIGURE-BEFORE-INIT.  The
    GAP G create-fresh `bd init -p <prefix>` ran WHILE `sync.remote` was STILL
    configured in `.beads/config.yaml` to the derived `<owner>/<bc>-beads`
    remote, which EXISTS but is EMPTY of Dolt data.  With `sync.remote`
    configured, `bd init` (like `bd bootstrap`) CLONES that empty remote and
    HARD-FAILS "Error 1105: clone failed; remote at that url contains no Dolt
    data", so the create-fresh never happens (GAP G's test false-greened
    because its fixture omitted this configured-empty-remote precondition;
    confirmed in a real in-container launch, v0.3.56).  So the create-fresh is
    wrapped: capture the scaffolded `sync.remote` line, REMOVE it from
    `.beads/config.yaml` so `bd init -p` create-freshes a PREFIXED local dolt DB
    with NO remote configured (does NOT clone), then RESTORE the line before the
    `bd dolt remote add origin` + `bd dolt push` seed configures the git+https
    dolt remote and seeds refs/dolt/*.

    lead-372r / GAP I (ROOT, additive to GAP H, which is UNCHANGED) —
    CLEAR-BEFORE-INIT.  At LAUNCH the PRECEDING failed `bd bootstrap` empty-remote
    clone LEAVES a PARTIAL `.beads/embeddeddolt` on disk.  With GAP H's
    `sync.remote` unconfigured, the create-fresh `bd init -p <prefix>` would still
    have run — except that partial embedded-Dolt working set makes `bd init -p`
    ABORT "database already exists; use bd init --force", a failure MASKED by the
    `|| true` on `bd init`.  So the create-fresh NEVER happens, the subsequent
    `bd dolt push` seeds nothing, and the fatal `git ls-remote refs/dolt` verify
    fails -> seed exit 1 -> BC offline (traced in-container, v0.3.57; GAP H's
    executed test false-greened because its fixture omitted the partial-DB
    precondition).  So immediately after the GAP H unconfigure and BEFORE
    `bd init -p`, `rm -rf .beads/embeddeddolt` clears any partial state (equivalent
    to `bd init --force`) so the create-fresh actually runs; `bd dolt push` then
    seeds THAT prefixed DB and the fatal verify passes.
    """
    # Strip the `git+` transport prefix so RAW git accepts the URL; the dolt
    # remote itself keeps the original `git+https://` scheme below.
    git_push_url = beads_remote_url.removeprefix("git+")
    return (
        f"set -e; cd {CONTAINER_WORKSPACE}; "
        # Materialize the committed registry into a throwaway init tree so the
        # seed commit carries the BC's tracked issues.
        "tmp=$(mktemp -d); "
        "cp .beads/issues.jsonl \"$tmp/issues.jsonl\" 2>/dev/null || true; "
        "git -C \"$tmp\" init -b main >/dev/null; "
        "git -C \"$tmp\" add -A; "
        "git -C \"$tmp\" -c user.email=bc-launcher@shopsystem "
        "-c user.name=bc-launcher commit -m 'seed beads remote' >/dev/null; "
        # NON-FATAL raw-git push to the PLAIN https:// URL: redundant (the
        # branch already exists) and must not abort the seed under set -e.
        f"git -C \"$tmp\" push \"{git_push_url}\" main >/dev/null 2>&1 || true; "
        # lead-vb6j / ROOT / GAP G — CREATE-FRESH-THEN-SEED ORDERING.  Establish a
        # PREFIXED local dolt DB create-fresh from the committed
        # `.beads/metadata.json` BEFORE the dolt remote is configured and BEFORE
        # `bd dolt push`.  ROOT (traced in-container, v0.3.55): with sync.remote
        # configured (GAP B) and the tracker remote EMPTY, `bd bootstrap`
        # dolt-CLONES the empty remote and HARD-FAILS "contains no Dolt data"
        # instead of create-fresh'ing from metadata.json — so at seed time there
        # is NO prefixed local DB, `bd dolt push` seeds nothing / a prefix-less
        # DB, and after standup `bd create` fails "issue_prefix config is missing"
        # (session-start health gate red -> BC offline).  `bd bootstrap` DOES
        # create-fresh ("Created fresh database with prefix") when NO remote is
        # configured (lead-pqlx); `bd init` is the create-fresh primitive that
        # likewise CREATES a fresh DB rather than cloning the configured remote.
        # So: read the COMMITTED prefix and `bd init -p` a fresh PREFIXED local
        # dolt DB with the dolt remote NOT yet configured.  The `bd dolt remote
        # add origin` + `bd dolt push` below then seed THAT prefixed DB, so
        # refs/dolt/* land WITH the prefix, the retried `bd bootstrap` exits
        # zero, and `bd create` in the new BC yields a `<prefix>-<n>` id.
        #
        # PREFIX SOURCE (lead-vb6j follow-up).  PRIMARY: `.beads/metadata.json`
        # `dolt_database` — real bd-written metadata.json carries `dolt_database`
        # (e.g. `shopsystem_bc_launcher`) and NO `issue_prefix` key.  FALLBACK:
        # the FIRST issue id in `.beads/issues.jsonl`, quote-BOUNDED and cut at
        # its FINAL hyphen — the shell mirror of `committed_beads_prefix_from_
        # registry` (regex `"id"\s*:\s*"([^"]+)"`, then `rsplit("-", 1)[0]`).
        # The capture MUST be quote-bounded (`[^"]*`): a greedy `"\(.*\)-[^-]*"`
        # bleeds across the multi-field JSONL line and yields a ~1000-char
        # garbage prefix.  NEVER fall back to a BC-name-derived prefix (lead-rply
        # / lead-vb6j): a cloned registry may carry a prefix the BC name does not
        # imply.
        "gapg_prefix=$(sed -n "
        "'s/.*\"dolt_database\"[[:space:]]*:[[:space:]]*\"\\([^\"]*\\)\".*/\\1/p' "
        ".beads/metadata.json | head -1); "
        "if [ -z \"$gapg_prefix\" ]; then "
        "gapg_id=$(grep -o '\"id\"[[:space:]]*:[[:space:]]*\"[^\"]*\"' "
        ".beads/issues.jsonl | head -1 | sed 's/.*\"\\([^\"]*\\)\"$/\\1/'); "
        "gapg_prefix=\"${gapg_id%-*}\"; fi; "
        # lead-tc38 / GAP H (ROOT, supersedes GAP G) — UNCONFIGURE sync.remote
        # BEFORE the `bd init -p` create-fresh, then RESTORE it before the dolt
        # seed.  GAP G ran `bd init -p` WHILE `sync.remote` was STILL configured
        # in `.beads/config.yaml` to the derived `<owner>/<bc>-beads` remote —
        # which EXISTS (GAP B resolved its owner + `create-absent` gh-created it)
        # but is EMPTY of Dolt data.  With `sync.remote` configured, `bd init`
        # (like `bd bootstrap`) CLONES that empty remote and HARD-FAILS
        # "Error 1105: clone failed; remote at that url contains no Dolt data" —
        # so the create-fresh never happens, `bd dolt push` seeds a prefix-less
        # / empty DB, and `bd create` after standup fails "issue_prefix config is
        # missing".  GAP G's structural test false-greened because its fixture
        # OMITTED this configured-empty-remote precondition; confirmed in a real
        # in-container launch (v0.3.56).  So: capture the scaffolded `sync.remote`
        # line, remove it from `.beads/config.yaml` so `bd init -p` create-freshes
        # a PREFIXED local dolt DB with NO remote configured (does NOT clone),
        # then restore the line before `bd dolt remote add`/`bd dolt push` seed
        # refs/dolt/* against the git+https dolt remote.
        "gaph_remote_line=$(grep -E '^sync\\.remote' .beads/config.yaml "
        "2>/dev/null | head -1 || true); "
        "sed -i '/^sync\\.remote/d' .beads/config.yaml 2>/dev/null || true; "
        # lead-372r / GAP I (ROOT, additive to GAP H) — CLEAR-BEFORE-INIT.  At
        # LAUNCH the PRECEDING failed `bd bootstrap` empty-remote clone LEAVES a
        # PARTIAL `.beads/embeddeddolt` on disk.  `bd init -p` then ABORTS
        # "database already exists; use bd init --force" — MASKED by the `|| true`
        # below — so the create-fresh NEVER runs, the `bd dolt push` seeds
        # nothing, and the fatal `git ls-remote refs/dolt` verify fails -> seed
        # exit 1 -> BC offline (traced in-container, v0.3.57; GAP H's fixture
        # omitted this partial-DB precondition so it false-greened over the
        # ordering).  So remove any partial embedded-Dolt working set BEFORE the
        # create-fresh, so `bd init -p` create-freshes a fresh PREFIXED local dolt
        # DB rather than aborting under the mask.  (`rm -rf` is equivalent to
        # `bd init --force` here; the lead's manual test only worked because it
        # `rm -rf`'d first — the launch does not.)
        "rm -rf .beads/embeddeddolt; "
        "BD_NON_INTERACTIVE=1 bd init -p \"$gapg_prefix\" >/dev/null 2>&1 || true; "
        # Restore the captured sync.remote line now that the fresh PREFIXED local
        # dolt DB exists, so the dolt seed below (and later `bd bootstrap` /
        # `bd create`) again see the configured tracker remote.
        "if [ -n \"$gaph_remote_line\" ]; then "
        "printf '%s\\n' \"$gaph_remote_line\" >> .beads/config.yaml; fi; "
        # Point the local bd working set at the now-initialized DOLT remote
        # (git+https:// — bd's own tooling handles that scheme) and push the
        # create-fresh'd PREFIXED embedded-Dolt working set up.  THIS is the step
        # that seeds refs/dolt/* — now carrying the committed prefix.
        f"bd dolt remote add origin {beads_remote_url} || true; "
        "bd dolt push || true; "
        # Verify the remote now carries Dolt data refs (raw git ls-remote, so
        # the PLAIN https:// URL again).
        f"git ls-remote {git_push_url} 'refs/dolt/*' | grep -q refs/dolt"
    )



# lead-r34c / GAP B — the scaffolded ORIGIN_OWNER placeholder in the tracker
# remote is correct at SCAFFOLD time (no origin owner is known yet), but it must
# be RESOLVED to the derived GitHub owner BEFORE the in-container `bd bootstrap`
# runs.  Empirically proven (David's 2026-07-07 shopsystem-knowledge standup):
# the standup resolved the owner to `dstengle` for the gh-create step but never
# wrote it back into the in-container `.beads` config / functional bd dolt
# remote, so `bd bootstrap` cloned the stale
# `git+https://github.com/ORIGIN_OWNER/<bc>-beads.git` URL and failed
# "Repository not found".  This writeback derives the owner from the CONTAINER's
# `/workspace` git origin remote (the real origin the clone left behind) and
# rewrites BOTH the `.beads/config.yaml` `sync.remote` AND the functional bd
# dolt remote (the one `bd dolt remote list` reports and `bd bootstrap` clones
# from), so no literal `ORIGIN_OWNER` segment survives to bootstrap time and the
# clone target is `<owner>/<bc>-beads`.  It is idempotent + best-effort: on a
# config already carrying a resolved owner the sed is a no-op and the dolt
# remote re-add is harmless.
def _resolve_origin_owner_writeback_script(bc_name: str) -> str:
    """Shell to RESOLVE the ORIGIN_OWNER placeholder to the derived GitHub owner
    and WRITE it into the in-container `.beads` config sync.remote + the
    functional bd dolt remote BEFORE `bd bootstrap` (lead-r34c / GAP B)."""
    remote_tail = f"{bc_name}-beads.git"
    return (
        f"set -e; cd {CONTAINER_WORKSPACE}; "
        # (1) Derive the GitHub owner from the container's /workspace git origin
        #     remote: strip a trailing `.git`, normalise a `git@host:owner/repo`
        #     scp-form colon to a slash, then take the second-to-last path
        #     segment (`<owner>` in `.../<owner>/<repo>`).
        f"url=$(git -C {CONTAINER_WORKSPACE} remote get-url origin); "
        "owner=$(printf '%s' \"$url\" | sed -E 's#\\.git$##; s#:#/#g' "
        "| awk -F/ '{print $(NF-1)}'); "
        "test -n \"$owner\"; "
        # (2) Rewrite the scaffolded `.beads` config sync.remote placeholder so
        #     no literal ORIGIN_OWNER survives in the tracker config.
        "if [ -f .beads/config.yaml ]; then "
        "sed -i \"s#ORIGIN_OWNER#${owner}#g\" .beads/config.yaml; fi; "
        # (3) Rewrite the FUNCTIONAL bd dolt remote (the one `bd dolt remote
        #     list` reports and `bd bootstrap` clones from): drop the stale
        #     ORIGIN_OWNER remote and re-add it under the derived owner so the
        #     bootstrap clone target is <owner>/<bc>-beads.
        f"remote_url=\"git+https://github.com/${{owner}}/{remote_tail}\"; "
        "bd dolt remote remove origin 2>/dev/null || true; "
        "bd dolt remote add origin \"$remote_url\" || true"
    )
