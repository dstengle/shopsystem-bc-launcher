# Runbook: recovering a beads Dolt remote wedged by schema skew (#4259)

Applies to **any** shop whose beads Dolt remote was last pushed by an older
`bd`. This is not a bug in one shop's tracker — it is a structural property of
the beads migration chain, so the same wall is waiting in every BC whose remote
has gone quiet for a release or two.

Recovery is **non-destructive if done in the order below**, and **destroys
issues if the force-push is done first**. Read the safety rule before running
anything.

## Symptom

At session-start the work-tracker health step reports the tracker locally
writable (`bd create` / `bd ready` exit zero) but the test `dolt push` fails:

```
Error: push to origin/main: Error 1105: unknown push error; no common ancestor
Local and remote Dolt histories have diverged.
```

Attempting to adopt the remote instead (`bd bootstrap`) then fails with either

```
refusing to auto-apply N pending schema migrations to a remote-backed database (v32 -> v53)
```

or, if you push past that gate:

```
Error: failed to open database: ... migrate: migration
0047_recompute_mixed_is_blocked.up.sql: Error 1146: table not found: wisps
```

## Why it happens

`wisps` is registered in `dolt_ignore`:

```sql
REPLACE INTO dolt_ignore VALUES ('wisps', true);
REPLACE INTO dolt_ignore VALUES ('wisp_%', true);
```

so the table is local-only and **never transfers on clone**. But
`0020_create_wisps` lives in the **main** migration series, and
`schema_migrations` **is** pushed. A clone therefore inherits a
`schema_migrations` asserting that `0020` was applied, while the `wisps` table
itself is absent. Any later main-series migration that references `wisps` —
`0047_recompute_mixed_is_blocked` is the first — then dies on a table that the
schema says exists.

**Blast radius:** any beads Dolt remote sitting at schema **20..46** that a
newer `bd` (v53) tries to adopt. It is not "the remote is too old"; it is that
ignored tables never transfer. A remote at v53 is unaffected — which is why
reseeding (step 5) permanently retires the wall for that shop.

The divergence and the schema wall usually arrive together, because the same
heal that rebuilt the local DB from scratch also gave it a fresh Dolt history.

## SAFETY RULE — read before running anything

**Never `bd dolt push --force` until you have proven local ⊇ remote.**

The remote is frequently **ahead** of both the local working set and the
committed `.beads/issues.jsonl`. In the incident this runbook came from, the
remote held 356 issues against local's 330 — a force-push "to fix the
divergence" would have destroyed 26 issues of real work. `bd`'s own error text
offers `bd dolt push --force` as recovery option 2 with no such warning.

Equally, do **not** reflexively `bd bootstrap` to "adopt the remote": on a
wedged remote that lands you on a database that cannot migrate forward.

Establish which side is authoritative by evidence (steps 2–3). Do not infer it
from which side looks newer, and do not trust a stale `issues.jsonl`.

## Procedure

### 1. Identify the remote's schema version

`bd`'s refusal names it (`v32 -> v53`). The remote's version equals the max
main-series migration of whichever `bd` last pushed it.

### 2. Get a `bd` that reads the remote natively

Find the release whose max main-series migration equals the remote's version —
that binary needs **no** migration to read it, so it never hits the wall.
Inspect any `bd` binary's max migration without running it:

```
strings -n 8 <path-to-bd> | grep -oE 'migrations/00[0-9]{2}_[a-z0-9_]+\.up\.sql' \
  | sed 's|migrations/||' | sort -u | tail -1
```

For a **v32** remote that is **bd v1.0.4** (max migration `0032`). Releases:
`https://github.com/gastownhall/beads/releases`

```
curl -sL -o bd104.tar.gz \
  https://github.com/gastownhall/beads/releases/download/v1.0.4/beads_1.0.4_linux_amd64.tar.gz
tar xzf bd104.tar.gz    # yields ./bd
```

Keep the old binary in a scratch dir. **Never put it on `PATH` and never let it
open the live `.beads/`** — an old `bd` against a v53 database is its own
incident.

### 3. Read the remote into a throwaway workspace and diff against local

Clone with the OLD binary, into a scratch dir with its own `dolt_database`
name, so the live tracker is untouched:

```
mkdir -p /tmp/probe/.beads && cd /tmp/probe && git init -b main .
echo 'sync.remote: "git+https://github.com/<org>/<bc-name>-beads.git"' > .beads/config.yaml
cat > .beads/metadata.json <<'EOF'
{"database":"dolt","backend":"dolt","dolt_mode":"embedded","dolt_database":"remote_readback"}
EOF
/path/to/old/bd bootstrap --yes
/path/to/old/bd export --all -o /tmp/probe/remote.jsonl
```

Then export local and compare **ID sets** — counts alone are not enough:

```
cd /path/to/bc && bd export --all -o /tmp/local.jsonl
```

Compare `remote.jsonl` vs `local.jsonl`: list remote-only IDs (what a
force-push would destroy) and local-only IDs (what adopting the remote would
destroy). Decide from that evidence.

### 4. Restore anything the remote has that local lacks

If a heal ran, its pre-heal export is the best source — check
`.beads/pre-heal-export.jsonl` first and verify it against the remote readback.
`bd import` is upsert, so it adds missing issues and updates existing ones:

```
bd import .beads/pre-heal-export.jsonl
```

**Before importing, compare `updated_at` on the shared issues in both
directions.** Import is an upsert: if the source file is *older* than local for
shared issues, it will silently revert them. Proceed only when the source is
newer-or-equal everywhere. (`bd import` keeps local state for same-timestamp
different-content rows unless you pass `--allow-stale`.)

Then re-verify: every remote ID must now be present locally. Only local-only
IDs may remain, and you must be able to account for each one.

### 5. Reseed the remote

Now that local is a proven superset, the force-push cannot destroy anything,
and it republishes the remote at the current schema — retiring the wall for
this shop permanently:

```
bd dolt push --force
```

### 6. Verify — with the CURRENT bd, not the probe

The health probe passing is necessary but weak. The real proof is that the
operation which previously failed now succeeds: a fresh `bd bootstrap` from the
reseeded remote using the **current** `bd`, reading the expected issue count
with no migration gate.

```
bd dolt push          # non-forced; must exit 0
# then, in a fresh scratch workspace, with the CURRENT bd:
bd bootstrap --yes && bd stats
```

### 7. Re-export and commit `issues.jsonl`

**Do not skip this.** The git-tracked registry is what `bd` auto-imports when a
local DB comes up empty. Leaving it stale means the next session re-seeds the
old working set and the incident repeats.

```
bd export --all -o .beads/issues.jsonl
git add .beads/issues.jsonl && git commit
```

Verify before committing that no ID present in `HEAD:.beads/issues.jsonl` is
missing from the new file.

## Notes for the heal path

If a shop's automated heal produced this state, check what it rebuilt *from*.
A heal that takes a correct pre-heal `bd export --all` and then rebuilds from
the committed `issues.jsonl` will silently drop every issue the remote had but
the committed file lacked. The pre-heal export is the source of truth; the
committed jsonl is a possibly-stale snapshot. See `shopsystem_bc_launcher-1f4n`
for the heal this applies to.
