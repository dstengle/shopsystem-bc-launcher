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
