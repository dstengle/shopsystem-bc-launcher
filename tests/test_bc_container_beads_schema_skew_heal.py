"""pytest-bdd binding + executed teeth for the remote-backed beads schema-skew
heal (lead-915f).

Pins the standup's beads-provisioning schema-skew heal: on detecting bd's
#4259 refusal to auto-apply schema migrations to a remote-backed DB, REBUILD a
fresh local dolt DB at the baked bd's CURRENT schema from the committed
`.beads/issues.jsonl` via `bd init --from-jsonl` (NOT in-place `bd migrate`,
proven DEAD at lead-065a), taking a pre-heal `bd export --all` safety net
FIRST, REFUSING for a lead-role beads (sole-clone invariant), and durably
reseeding the remote via a brokered force-push.

ADDITIVE to the empty-remote provisioning family (bootstrap-resilience
@scenario_hash ada742d33c996d34 + GAP D/E/G/H/I) — retires/supersedes NOTHING.

The 5 gherkin scenarios are bound (and genuinely EXECUTED) via
tests/steps/schema_skew.py.  This module adds the predicate, wiring, and
structural-ordering teeth, plus the df748234563bdedb positive-row xfail
(PARTIAL DELIVERY: the positive row turns green only once the lead-tc38
brokered dolt-push credential path is wired).
"""
from __future__ import annotations

import subprocess

import pytest
from pytest_bdd import scenarios

from bc_launcher.controller import (
    CONTAINER_WORKSPACE,
    PRE_HEAL_SUBSET_ABORT_BANNER,
    _is_empty_remote_failure,
    _is_schema_skew_migration_refusal,
    _schema_skew_heal_script,
)

scenarios("../features/bc_container_beads_schema_skew_heal.feature")

_BEADS_REMOTE = "git+https://github.com/dstengle/shopsystem-bc-launcher-beads.git"

_4259_ERROR = (
    "Bootstrap failed: 21 schema migrations (v32 -> v53) that bd will not "
    "auto-apply to a remote-backed database (fork hazard, bd upstream #4259); "
    "refusing to migrate a remote-backed database"
)


# ---------------------------------------------------------------------------
# Predicate: classify bd's #4259 remote-backed migration refusal, DISTINCT
# from the empty-remote family's "no branches" / "contains no Dolt data".
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    _4259_ERROR,
    "bd will not auto-apply schema migrations to a remote-backed database (#4259)",
    "refusing to migrate a remote-backed database: fork hazard #4259",
    "cannot auto-apply 21 migrations (v32 -> v53) to a remote-backed database",
])
def test_schema_skew_refusal_classified(text):
    assert _is_schema_skew_migration_refusal(text), (
        f"must classify the #4259 remote-backed migration refusal: {text!r}"
    )


def test_schema_skew_predicate_distinct_from_empty_remote():
    """The schema-skew refusal is DISTINCT from the empty-remote family: an
    empty-remote error must NOT be classified as a schema-skew refusal, and the
    #4259 refusal must NOT be classified as an empty-remote failure — the two
    heals must not misfire on each other's precondition (lead-915f)."""
    empty_remote = (
        "dolt clone git+https://github.com/dstengle/x-beads.git: git remote "
        "has no branches; initialize the repository with an initial "
        "branch/commit first"
    )
    contains_no_dolt = "clone failed; remote at that url contains no Dolt data"
    assert not _is_schema_skew_migration_refusal(empty_remote)
    assert not _is_schema_skew_migration_refusal(contains_no_dolt)
    assert not _is_empty_remote_failure(_4259_ERROR)


# ---------------------------------------------------------------------------
# Structural-ordering backstop for the executed heal: pre-heal export FIRST,
# then destroy, then rebuild-from-jsonl, then reseed force-push; lead refusal;
# idempotent no-op guard first of all.
# ---------------------------------------------------------------------------

def test_schema_skew_heal_script_ordering():
    script = _schema_skew_heal_script(_BEADS_REMOTE, "bc")
    i_ready = script.index("bd ready")
    i_export = script.index("bd export --all")
    i_destroy = script.index("rm -rf .beads/embeddeddolt")
    i_init = script.index("bd init --from-jsonl")
    i_push = script.index("bd dolt push")
    assert i_ready < i_export < i_destroy < i_init < i_push, (
        "ordering must be: bd ready (no-op guard) < bd export --all (pre-heal "
        "safety net) < rm -rf embeddeddolt (destroy) < bd init --from-jsonl "
        "(rebuild) < bd dolt push (reseed); got "
        f"ready@{i_ready}, export@{i_export}, destroy@{i_destroy}, "
        f"init@{i_init}, push@{i_push} (lead-915f)"
    )
    assert "bd migrate" not in script, (
        "the heal must NEVER attempt an in-place `bd migrate` — #4259 refuses "
        "it and lead-065a proved it hard-fails at migration 0047 (lead-915f)"
    )
    # the reseed is history-replacing (force-push).
    assert "bd dolt push --force" in script


def test_schema_skew_heal_lead_refusal_guard_before_destroy():
    """The lead-role refusal guard must appear BEFORE any destructive step so a
    lead-role beads is refused without a rebuild/reseed ever running (the
    sole-clone invariant holds only for a BC) (lead-915f)."""
    script = _schema_skew_heal_script(_BEADS_REMOTE, "lead")
    assert "lead" in script
    i_guard = script.index("shop_type=")
    i_destroy = script.index("rm -rf .beads/embeddeddolt")
    i_init = script.index("bd init --from-jsonl")
    assert i_guard < i_destroy < i_init


# ---------------------------------------------------------------------------
# lead-wpnv3 / shopsystem_bc_launcher-1ttf — PRE-HEAL-EXPORT-AHEAD SAFETY NET.
#
# ADDITIVE to @scenario_hash:fbf7480ef25f766c, which pins (a) the committed
# `.beads/issues.jsonl` as the rebuild's source of truth and (b) the negative
# row "pre-heal export UNREADABLE -> proceed from jsonl anyway".  Both rows
# continue to hold UNCHANGED.  What NEITHER row addresses — and what this BC's
# own dogfooding incident proved is not hypothetical — is the case where the
# pre-heal export is READABLE AND CORRECT BUT AHEAD of the committed jsonl:
# the live DB carried 356 issues, the committed jsonl 330, and the heal
# rebuilt from the 330 per the letter of the pin, SILENTLY DROPPING 26 issues
# (two complete epics), then chased it with a history-replacing force-push.
#
# The invariant that makes fbf7480ef25f766c's "rebuild from the committed
# jsonl" step SAFE is exactly `export_ids ⊆ committed_ids`.  When that holds,
# the committed jsonl is a superset and the rebuild loses no issue.  When it
# does NOT hold, the rebuild is a silent deletion.  So the heal now PROVES the
# subset invariant BEFORE any destructive step and ABORTS (non-zero, naming the
# specific dropped IDs) when it cannot — it never silently rebuilds from a
# source it has proven to be lossy.
#
# WHY ABORT (option b) RATHER THAN AUTO-REBUILD FROM THE EXPORT (option a):
# see the _pre_heal_export_subset_guard docstring in tracker_provision.py.
# ---------------------------------------------------------------------------

def _write_beads_jsonl(path, issue_ids):
    """Write a realistic beads jsonl: `_type:issue` records carrying ids, plus
    a `_type:memory` record (which carries NO id) — the real
    `.beads/issues.jsonl` and `bd export --all` capture both, so the ID
    extraction must key off `_type` rather than assume every record is an
    issue."""
    lines = [
        '{"_type":"memory","key":"k1","value":"a memory record has no id"}'
    ]
    lines += [
        f'{{"_type":"issue","id":"{i}","title":"issue {i}","status":"open",'
        f'"priority":1}}'
        for i in issue_ids
    ]
    path.write_text("\n".join(lines) + "\n")


_AHEAD_BD_STUB = (
    'bd() { '
    '  if [ "$1" = "ready" ]; then return 1; fi; '
    '  if [ "$1" = "export" ]; then '
    '    if [ -f .beads/EXPORT_FAILS ]; then return 1; fi; '
    '    cat .beads/LIVE_EXPORT.jsonl; return 0; '
    '  fi; '
    '  if [ "$1" = "init" ]; then '
    '    mkdir -p .beads/embeddeddolt; : > .beads/HEALTHY; '
    '    printf rebuilt > "$PROBE/at_init"; return 0; '
    '  fi; '
    '  if [ "$1" = "dolt" ] && [ "$2" = "push" ]; then '
    '    printf pushed > "$PROBE/at_push"; return 0; '
    '  fi; '
    '  return 0; '
    '}; '
)


def _run_heal(ws, probe, shop_type="bc"):
    """Execute the LIVE `_schema_skew_heal_script` body against a fixture
    workspace with `bd` stubbed — real teeth on the shipped script, not a
    re-implementation."""
    script = _schema_skew_heal_script(_BEADS_REMOTE, shop_type)
    prefix = f"set -e; cd {CONTAINER_WORKSPACE}; "
    assert script.startswith(prefix), script[:120]
    body = "set -e; " + script[len(prefix):]
    return subprocess.run(
        ["bash", "-c", f'PROBE="{probe}"; ' + _AHEAD_BD_STUB + body],
        cwd=str(ws), capture_output=True, text=True,
    )


def _skewed_ws(tmp_path, committed_ids, live_ids, export_fails=False):
    """A remote-backed DB at an OLD schema (bd ready fails, embeddeddolt
    present) whose LIVE working set carries `live_ids` while the COMMITTED
    `.beads/issues.jsonl` carries `committed_ids`."""
    ws = tmp_path / "ws"
    beads = ws / ".beads"
    beads.mkdir(parents=True)
    probe = tmp_path / "probe"
    probe.mkdir()
    _write_beads_jsonl(beads / "issues.jsonl", committed_ids)
    _write_beads_jsonl(beads / "LIVE_EXPORT.jsonl", live_ids)
    (beads / "embeddeddolt").mkdir()
    (beads / "embeddeddolt" / "OLD_SCHEMA").write_text("v32 remote-backed\n")
    if export_fails:
        (beads / "EXPORT_FAILS").write_text("")
    return ws, beads, probe


# The real incident shape (shopsystem_bc_launcher-1ttf): the live DB held 356
# issues, the committed jsonl 330, and the 26-issue difference was two complete
# epics (1f4n and vipd) that the heal silently destroyed.
_COMMITTED_330 = [f"bclaunch-{i}" for i in range(1, 331)]
_DROPPED_26 = (
    [f"bclaunch-1f4n-{i}" for i in range(1, 14)]
    + [f"bclaunch-vipd-{i}" for i in range(1, 14)]
)
_LIVE_356 = _COMMITTED_330 + _DROPPED_26


def test_heal_aborts_when_pre_heal_export_is_ahead_of_committed_jsonl(tmp_path):
    """CRITERION 2 + 5 — the real incident shape, reproduced.

    Pre-heal export = 356 issue IDs, committed jsonl = 330, difference = 26.
    The heal MUST NOT silently rebuild from the 330.  It aborts non-zero,
    names the specific dropped IDs, and — because the abort precedes the
    destructive step — leaves every one of the 356 live issues intact
    (lead-wpnv3 / shopsystem_bc_launcher-1ttf)."""
    ws, beads, probe = _skewed_ws(tmp_path, _COMMITTED_330, _LIVE_356)
    assert len(_LIVE_356) == 356 and len(_COMMITTED_330) == 330
    assert len(set(_LIVE_356) - set(_COMMITTED_330)) == 26

    result = _run_heal(ws, probe)

    # (1) it must FAIL, not silently succeed on a lossy rebuild.
    assert result.returncode != 0, (
        "the heal must ABORT when the pre-heal export carries issues absent "
        "from the committed issues.jsonl — rebuilding from the committed 330 "
        "silently destroys the 26 uncommitted issues (lead-wpnv3); got exit "
        f"0 with stdout={result.stdout!r}"
    )
    # (2) ZERO silent loss: the destructive step must never have run, so the
    #     live DB (and its 26 at-risk issues) is still there to recover from.
    assert (beads / "embeddeddolt").exists(), (
        "the abort must precede the destroy step so the live DB survives "
        "intact for manual reconciliation (lead-wpnv3)"
    )
    assert not (probe / "at_init").exists(), (
        "no from-jsonl rebuild may run once the subset invariant is violated"
    )
    assert not (probe / "at_push").exists(), (
        "no history-replacing reseed force-push may run once the subset "
        "invariant is violated — it would propagate the loss to the remote"
    )
    # (3) the error must NAME the specific dropped IDs and direct manual
    #     reconciliation (a bare non-zero exit is not actionable).
    err = result.stderr
    for dropped_id in _DROPPED_26:
        assert dropped_id in err, (
            f"the abort must name the specific dropped issue id {dropped_id!r} "
            f"so the operator can reconcile it (lead-wpnv3); stderr={err!r}"
        )
    assert "26" in err, (
        f"the abort should surface the count of at-risk issues; stderr={err!r}"
    )


def test_heal_abort_does_not_name_issues_present_in_both(tmp_path):
    """The abort names only the AT-RISK ids (export minus committed), not the
    ids the committed jsonl already carries — an operator reconciling by hand
    must not be handed 356 ids when only 26 are at risk (lead-wpnv3)."""
    ws, beads, probe = _skewed_ws(tmp_path, _COMMITTED_330, _LIVE_356)
    result = _run_heal(ws, probe)
    assert result.returncode != 0
    # a memory record carries no id and must not be mistaken for a dropped issue.
    assert "memory" not in result.stderr.lower()
    # ids common to both sides are NOT at risk and must not be listed.
    assert "bclaunch-200" not in result.stderr, (
        "an id present in BOTH the export and the committed jsonl is not at "
        f"risk and must not be named as dropped; stderr={result.stderr!r}"
    )


def test_heal_proceeds_from_committed_jsonl_when_export_is_a_subset(tmp_path):
    """CRITERION 3 — when the pre-heal export IS a subset of the committed
    issues.jsonl, the committed jsonl is a superset and the rebuild loses
    nothing, so fbf7480ef25f766c's pinned behavior (rebuild from the committed
    jsonl) is UNCHANGED (lead-wpnv3)."""
    live_subset = [f"bclaunch-{i}" for i in range(1, 100)]
    ws, beads, probe = _skewed_ws(tmp_path, _COMMITTED_330, live_subset)

    result = _run_heal(ws, probe)

    assert result.returncode == 0, (
        "a pre-heal export that is a SUBSET of the committed jsonl loses "
        "nothing on rebuild; the heal must proceed unchanged (lead-wpnv3); "
        f"stderr={result.stderr!r}"
    )
    assert (probe / "at_init").exists(), (
        "the from-jsonl rebuild must still run in the subset case (criterion 3)"
    )


def test_heal_proceeds_from_committed_jsonl_when_export_equals_committed(tmp_path):
    """CRITERION 3 — an EQUAL id set is a subset; behavior unchanged."""
    ws, beads, probe = _skewed_ws(tmp_path, _COMMITTED_330, list(_COMMITTED_330))
    result = _run_heal(ws, probe)
    assert result.returncode == 0, (
        f"an equal id set loses nothing on rebuild; stderr={result.stderr!r}"
    )
    assert (probe / "at_init").exists()


def test_heal_proceeds_from_committed_jsonl_when_export_fails(tmp_path):
    """CRITERION 4 — when the pre-heal export step itself fails (old DB
    unreadable), fbf7480ef25f766c's existing negative row still holds: the heal
    proceeds from the committed issues.jsonl rather than aborting.  A failed
    export yields NO knowledge of the live DB, so the new subset guard must not
    fire on it (lead-wpnv3)."""
    ws, beads, probe = _skewed_ws(
        tmp_path, _COMMITTED_330, _LIVE_356, export_fails=True,
    )

    result = _run_heal(ws, probe)

    assert result.returncode == 0, (
        "with the pre-heal export FAILING, the heal must still rebuild from "
        "the committed issues.jsonl rather than aborting — the "
        "fbf7480ef25f766c negative row is unchanged (lead-wpnv3); "
        f"stderr={result.stderr!r}"
    )
    assert (probe / "at_init").exists(), (
        "the from-jsonl rebuild must still run when the export fails "
        "(criterion 4)"
    )


def test_subset_guard_runs_before_the_destructive_step():
    """Structural backstop: the subset guard must sit AFTER the pre-heal export
    (it needs the capture) and BEFORE the destroy — an abort after the destroy
    would leave the BC with neither a live DB nor a rebuild (lead-wpnv3)."""
    script = _schema_skew_heal_script(_BEADS_REMOTE, "bc")
    i_export = script.index("bd export --all")
    i_guard = script.index(PRE_HEAL_SUBSET_ABORT_BANNER)
    i_destroy = script.index("rm -rf .beads/embeddeddolt")
    assert i_export < i_guard < i_destroy, (
        "the pre-heal-export subset guard must run after the export and "
        "before the destroy; got export@{}, guard@{}, destroy@{} "
        "(lead-wpnv3)".format(i_export, i_guard, i_destroy)
    )
    # the guard is a *precondition*, so it must gate the rebuild+reseed too.
    assert i_guard < script.index("bd init --from-jsonl") < script.index(
        "bd dolt push"
    )


# ---------------------------------------------------------------------------
# Wiring: _provision_beads_tracker must, on a #4259 refusal, run the schema-skew
# heal (NOT leave the BC stranded), then reach online — genuinely exercised by
# driving the real BeadsProvisioningMixin against a fake driver that emits the
# #4259 refusal on `bd bootstrap`.
# ---------------------------------------------------------------------------

class _CompletedLike:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _SchemaSkewFakeDriver:
    """Minimal driver: first `bd bootstrap` fails with the #4259 refusal; the
    schema-skew heal exec succeeds; everything else is a no-op success.  Records
    exec_run commands so the wiring can be asserted."""

    def __init__(self):
        self.exec_calls = []

    def exec_run(self, container, command, user=None, env=None):
        self.exec_calls.append(command)
        joined = " ".join(command) if isinstance(command, list) else str(command)
        # `bd bootstrap` -> #4259 remote-backed migration refusal.
        if "bd bootstrap" in joined:
            return _CompletedLike(1, "", _4259_ERROR)
        # the shop-type read (.claude/shop/type.md) -> a BC.
        if command[:1] == ["cat"] and "type.md" in joined:
            return _CompletedLike(0, "bc\n", "")
        # the schema-skew heal script (recognised by its from-jsonl rebuild).
        if "bd init --from-jsonl" in joined:
            return _CompletedLike(0, "schema-skew heal: rebuilt fresh "
                                  "current-schema DB from committed issues.jsonl\n", "")
        return _CompletedLike(0, "", "")


def test_provision_beads_tracker_runs_schema_skew_heal_on_4259():
    from bc_launcher.controller import BcContainerController

    driver = _SchemaSkewFakeDriver()
    controller = BcContainerController(driver)
    out_lines, err_lines = [], []
    controller._provision_beads_tracker(
        "bc-shopsystem-bc-launcher", "shopsystem-bc-launcher",
        out_lines, err_lines,
    )

    heal_calls = [
        c for c in driver.exec_calls
        if "bd init --from-jsonl" in (" ".join(c) if isinstance(c, list) else str(c))
    ]
    assert heal_calls, (
        "on a #4259 remote-backed migration refusal the launcher must run the "
        "schema-skew heal (rebuild from jsonl), not strand the BC (lead-915f)"
    )
    # the heal succeeded -> a success/heal line, not a bare bootstrap-failure
    # strand warning as the only output.
    assert any("schema-skew heal" in ln for ln in out_lines), (
        "a successful schema-skew heal must be surfaced in the out lines "
        f"(lead-915f); out_lines={out_lines!r} err_lines={err_lines!r}"
    )


def test_provision_beads_tracker_4259_not_treated_as_empty_remote():
    """The #4259 refusal must NOT be misrouted through the empty-remote seed
    path (that path fires only when the remote carries NO Dolt data; here it
    carries data at a skewed old schema) (lead-915f)."""
    from bc_launcher.controller import BcContainerController

    driver = _SchemaSkewFakeDriver()
    controller = BcContainerController(driver)
    controller._provision_beads_tracker(
        "bc-shopsystem-bc-launcher", "shopsystem-bc-launcher", [], [],
    )
    seed_calls = [
        c for c in driver.exec_calls
        if "git ls-remote" in (" ".join(c) if isinstance(c, list) else str(c))
    ]
    assert not seed_calls, (
        "the #4259 refusal must not trigger the empty-remote seed script "
        "(lead-915f)"
    )
