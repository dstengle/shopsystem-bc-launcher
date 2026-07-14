"""lead-6ev8 behavior 4 (@scenario_hash:591515631f39c311) — the PERSISTENT-429
TEMPORAL-AND-BEHAVIORAL boundary.

Behaviors 1&2 gave every fabro LLM/ACP node a BOUNDED retry budget
(``max_retries=N`` -> the REAL fabro runtime honors it as max_attempts=N+1 > 1)
+ ``retry_policy="exponential"`` (spaced, increasing, bounded backoff), so a
transient 429 is not terminal on the FIRST error.  lead-01jw.3 (referenced here
BY VALUE, NOT re-pinned) made the terminal failsafe report DIAGNOSTIC: emit_blk's
``classify_reason`` derivation maps a 429 / rate-limit failing-node/context to
reason-class ``infra-path`` + detail-marker ``rate-limit-429`` (scenarios
738f35759127fe7f / 629be1e0224f3a03 in the failsafe-block-diagnostic feature).

This behavior pins the TEMPORAL-AND-BEHAVIORAL BOUNDARY that binds those two
already-shipped mechanisms into ONE contract: when a transient 429 PERSISTS past
the node's entire retry budget, the node reaches exhaustion ONLY AFTER a BOUNDED
retry effort (max_attempts > 1 — the multiple spaced attempts DID occur), and
ONLY THEN does the run block with the EXISTING infra-path / rate-limit-429
diagnostic.  The diagnostic block is the END of a bounded retry effort, not the
response to a single transient error — so exhaustion is a genuine CAPACITY
failure, never the pre-fix max_attempts=1 fail-fast.

FIDELITY (bind the REAL mechanisms, reference lead-01jw.3 by value):

  * The BINDING itself is named ONCE in ``bc_launcher.transient_resilience`` (the
    module whose charter is to be the single place naming the shared
    transient-error resilience contract, so the runtimes cannot silently
    diverge).  Behavior 3 named the SURVIVABLE-burst parity there; this names the
    complementary PERSISTENT-burst exhaustion facet.  The predicate is fed REAL
    values on both sides — the committed classify node's parsed
    (max_retries, retry_policy) and the lead-01jw.3-derived (reason_class,
    detail_marker) — so it is not a constant-equals-constant tautology.

  * TEMPORAL leg (real fabro binary, honest SKIP): the committed LLM/ACP retry
    budget yields max_attempts > 1 at the REAL fabro runtime (a bounded retry
    effort WOULD occur before exhaustion), with the NEGATIVE CONTROL that the
    pre-fix bare ``retry=N`` yields max_attempts == 1 (the fail-fast this
    boundary excludes).  Real inter-attempt WALL-CLOCK timing is NOT observable
    in-container (a real persistent-failure run blocks for many minutes on the
    exponential backoff before exhausting), so the bounded-effort fact is bound
    at the BUDGET level (max_attempts) via ``--dry-run`` ``stage.started``, not
    by running to wall-clock exhaustion.

  * DIAGNOSTIC-LINKAGE leg (reference by value): the SHIPPED ``classify_reason``
    derivation, extracted VERBATIM from the REAL committed emit_blk node and run
    over the persistent-429 fault drawn from lead-01jw.3's OWN pinned Examples
    table, derives exactly (infra-path, rate-limit-429) — and the bounded-budget
    LLM/ACP nodes route ``-> emit_blk [condition="outcome=failed"]`` on
    exhaustion, so a persistently-429'd node reaches THAT diagnostic at the end
    of its bounded effort.  This runs the exact same shipped mechanism the
    lead-01jw.3 tests pin; it does NOT re-pin or duplicate those scenarios.

ADDITIVE: this scenario references (does not re-pin) 738f35759127fe7f /
629be1e0224f3a03 and builds on behaviors 1-3; it modifies neither the emit_blk
classify_reason nor the failsafe-block-diagnostic feature.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

import bc_launcher.transient_resilience as tr
from bc_launcher.controller import _fabro_def_asset_root

# REUSE the REAL fabro-runtime harness + committed-def helpers from behavior 1
# (no copy-paste of the ~150-line isolated-server class).
from tests.test_lead_6ev8_fabro_llm_transient_retry import (
    _FabroRuntime,
    _max_retries,
    _node_body,
    _workflow_text,
)
from tests.support.container import _ky63_locate_or_fetch_fabro

# REFERENCE lead-01jw.3 BY VALUE: run the exact same shipped classify_reason
# derivation and read the same pinned Examples table the diagnostic tests pin.
from tests.test_lead_01jw3_fabro_failsafe_block_diagnostic import (
    _derive,
    _examples_rows,
)


_FEATURE = (
    Path(__file__).resolve().parent.parent
    / "features"
    / "bc_container_fabro_llm_transient_retry.feature"
)
_DIAGNOSTIC_FEATURE = (
    Path(__file__).resolve().parent.parent
    / "features"
    / "bc_container_fabro_failsafe_block_diagnostic.feature"
)
_BEHAVIOR_4_HASH = "591515631f39c311"

# The lead-01jw.3 diagnostic scenarios this behavior REFERENCES BY VALUE (and
# must NOT re-pin / duplicate into this feature file).
_REFERENCED_DIAGNOSTIC_HASHES = ("738f35759127fe7f", "629be1e0224f3a03")

# The LLM/ACP judgment nodes that BOTH carry the bounded retry budget AND route
# to the emit_blk diagnostic on retry EXHAUSTION (outcome=failed) — so a node
# whose transient 429 persists past its budget reaches the rate-limit-429
# diagnostic at the END of its bounded effort.  (`review` is excluded on
# purpose: its outcome=failed stage error routes to `halt`, not emit_blk.)
_BOUNDED_BUDGET_DIAGNOSTIC_NODES = ("classify", "suff", "plan", "impl", "impl_f")

# The persistent-429 fault class is pinned by lead-01jw.3's own Examples row.
_RATE_LIMIT_EXAMPLE_ROW = ("infra-path", "rate-limit-429")


def _retry_policy(body: str) -> str | None:
    """The node's declared ``retry_policy="..."`` (the fabro backoff selector),
    or None if absent."""
    m = re.search(r'\bretry_policy="([^"]+)"', body)
    return m.group(1) if m else None


def _routes_to_emit_blk_on_failure(graph: str, node: str) -> bool:
    """True iff the graph carries ``<node> -> emit_blk [condition="outcome=failed"]``
    — the exhaustion edge that reaches the diagnostic block."""
    pat = rf'(?m)^\s*{re.escape(node)}\s*->\s*emit_blk\s*\[condition="outcome=failed"\]'
    return re.search(pat, graph) is not None


def _persistent_429_fault() -> str:
    """The persistent-429 ``<fault>`` cell drawn from lead-01jw.3's OWN pinned
    Examples table (738f35759127fe7f) — the row whose (reason_class,
    detail_marker) is (infra-path, rate-limit-429).  Referencing the pinned spec
    by value rather than hard-coding a fault string."""
    for row in _examples_rows("738f35759127fe7f"):
        if (row["reason_class"], row["detail_marker"]) == _RATE_LIMIT_EXAMPLE_ROW:
            return row["fault"]
    raise AssertionError(
        "lead-01jw.3 Examples table 738f35759127fe7f has no "
        f"{_RATE_LIMIT_EXAMPLE_ROW} row to reference by value"
    )


# ===========================================================================
# Block-only scenario-hash pin (block-only recompute must equal the on-disk tag).
# ===========================================================================

@pytest.mark.skipif(
    shutil.which("scenarios") is None,
    reason="canonical `scenarios` CLI not on PATH",
)
def test_scenario_block_recomputes_to_its_pin():
    """The block-only hash of scenario 591515631f39c311 recomputes to its tag,
    and behaviors 1-3's pins in this feature stay undisturbed.

    RED (pre-bind): the boundary scenario is not yet appended to the feature
    file, so it is not pinned here and this REDs.
    """
    from tests.test_lead_6ev8_fabro_llm_transient_retry import _scenario_blocks

    blocks = _scenario_blocks(_FEATURE.read_text(encoding="utf-8"))
    assert _BEHAVIOR_4_HASH in blocks, (
        f"No scenario tagged @scenario_hash:{_BEHAVIOR_4_HASH} in {_FEATURE.name}"
    )
    recomputed = subprocess.run(
        ["scenarios", "hash"],
        input=blocks[_BEHAVIOR_4_HASH],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert recomputed == _BEHAVIOR_4_HASH, (
        f"scenario block recomputed to {recomputed!r} but the feature pins "
        f"@scenario_hash:{_BEHAVIOR_4_HASH}; re-tag or revert the edit"
    )
    # behaviors 1-3's pins in this feature stay undisturbed.
    for h in ("3b3cf899ddd8ed68", "088460f2fd9490a4", "acd8d90bd9d4e4df"):
        got = subprocess.run(
            ["scenarios", "hash"],
            input=blocks[h],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert got == h, f"pin {h} disturbed: recomputed {got!r}"


# ===========================================================================
# The BINDING — the temporal-and-behavioral contract named ONCE in the shared
# transient-resilience anchor, fed REAL values on both sides (the honest RED:
# this facet of the contract is not yet expressed anywhere).
# ===========================================================================

def test_persistent_429_exhaustion_binding_is_bounded_then_diagnostic():
    """The shared transient-resilience anchor names the PERSISTENT-429 exhaustion
    contract: a persistent 429 blocks ONLY AFTER a bounded retry effort AND the
    terminal report is the EXISTING infra-path / rate-limit-429 diagnostic.

    Fed REAL values on both sides so it is not a tautology:
      * the bounded-effort half = the committed classify node's parsed
        (max_retries, retry_policy) — behaviors 1&2;
      * the diagnostic half = the lead-01jw.3-derived (reason_class,
        detail_marker) for the persistent-429 fault (referenced by value).

    RED (pre-bind): ``transient_resilience`` names only the survivable-burst
    parity (behavior 3); the persistent-burst exhaustion facet is not yet named,
    so the predicate/markers do not exist and this REDs.
    """
    assert hasattr(tr, "PERSISTENT_429_REASON_CLASS") and hasattr(
        tr, "PERSISTENT_429_DETAIL_MARKER"
    ), (
        "transient_resilience must name the EXISTING lead-01jw.3 infra-path / "
        "rate-limit-429 diagnostic markers it references by value"
    )
    assert hasattr(tr, "persistent_429_exhaustion_is_bounded_then_diagnostic"), (
        "transient_resilience must name the persistent-429 exhaustion contract "
        "binding the bounded retry effort to the existing rate-limit-429 "
        "diagnostic (the temporal-and-behavioral boundary lead-6ev8 behavior 4 "
        "pins)"
    )

    # The markers the anchor references BY VALUE are exactly lead-01jw.3's.
    assert tr.PERSISTENT_429_REASON_CLASS == "infra-path"
    assert tr.PERSISTENT_429_DETAIL_MARKER == "rate-limit-429"

    # REAL bounded-effort half: the committed classify LLM node's budget+policy.
    classify_body = _node_body(_workflow_text(), "classify")
    max_retries = _max_retries(classify_body)
    retry_policy = _retry_policy(classify_body)

    # REAL diagnostic half: the lead-01jw.3 shipped derivation over the pinned
    # persistent-429 fault (referenced by value).
    emit_blk_body = _node_body(_workflow_text(), "emit_blk")
    reason_class, detail_marker = _derive(emit_blk_body, "", _persistent_429_fault())
    assert (reason_class, detail_marker) == _RATE_LIMIT_EXAMPLE_ROW

    # The contract holds for the REAL committed values.
    assert tr.persistent_429_exhaustion_is_bounded_then_diagnostic(
        max_retries, retry_policy, reason_class, detail_marker
    ), (
        "the committed classify budget + the lead-01jw.3 rate-limit-429 "
        "diagnostic must satisfy the persistent-429 bounded-then-diagnostic "
        f"contract; got max_retries={max_retries!r} retry_policy={retry_policy!r} "
        f"reason_class={reason_class!r} detail_marker={detail_marker!r}"
    )

    # NEGATIVE CONTROLS — the predicate has teeth on both halves:
    #  * a fail-fast budget (no max_retries -> max_attempts=1) is NOT bounded
    #    retry effort, so it fails the contract even with the right diagnostic;
    assert not tr.persistent_429_exhaustion_is_bounded_then_diagnostic(
        None, retry_policy, reason_class, detail_marker
    ), "a fail-fast (max_attempts=1) budget must NOT satisfy the bounded contract"
    #  * an immediate-retry (no exponential policy) is not the bounded-spaced
    #    effort the boundary requires;
    assert not tr.persistent_429_exhaustion_is_bounded_then_diagnostic(
        max_retries, None, reason_class, detail_marker
    ), "an unspaced retry storm must NOT satisfy the bounded contract"
    #  * a non-rate-limit terminal diagnostic is not the referenced report.
    assert not tr.persistent_429_exhaustion_is_bounded_then_diagnostic(
        max_retries, retry_policy, "deliverable-gate", "deliverable"
    ), "a non-(infra-path/rate-limit-429) diagnostic must NOT satisfy the contract"


# ===========================================================================
# TEMPORAL leg — the REAL fabro runtime proves the bounded retry effort WOULD
# occur before exhaustion (max_attempts > 1), with the fail-fast negative
# control.  Honest SKIP if the binary / server cannot be obtained.
# ===========================================================================

@pytest.fixture(scope="module")
def fabro_runtime(tmp_path_factory):
    fabro, note = _ky63_locate_or_fetch_fabro()
    if fabro is None:
        pytest.skip(
            f"fabro binary could not be obtained; real-runtime bounded-effort "
            f"leg deferred honestly. reason: {note!r}"
        )
    home = tmp_path_factory.mktemp("fabro6ev8b4home")
    rt = _FabroRuntime(fabro, home)
    if not rt.start():
        log = ""
        try:
            log = (home / "server.log").read_text()[-1500:]
        except OSError:
            pass
        rt.stop()
        pytest.skip(
            "a local ephemeral fabro server could not be started for the "
            f"real-runtime bounded-effort leg (honest SKIP). server.log tail:"
            f"\n{log}"
        )
    try:
        yield rt
    finally:
        rt.stop()


def test_committed_classify_budget_yields_bounded_effort_before_exhaustion(
    fabro_runtime,
):
    """TEMPORAL boundary at the REAL fabro runtime: the committed classify LLM
    node's declared retry budget yields max_attempts > 1 — so on a PERSISTENT
    429 the node makes MULTIPLE attempts (a bounded retry effort) before reaching
    exhaustion, rather than terminating on the first transient error.

    Negative control: the pre-fix bare ``retry=N`` yields max_attempts == 1 — the
    fail-fast path this boundary excludes ("the multiple spaced attempts DID
    occur" would be FALSE there).
    """
    body = _node_body(_workflow_text(), "classify")
    budget = _max_retries(body)
    assert budget is not None and budget >= 1, (
        "the committed classify LLM node must declare an EFFECTIVE `max_retries=N`"
        f" budget so a bounded retry effort occurs before exhaustion; body:\n{body}"
    )
    ma_bounded = fabro_runtime.probe_max_attempts(f"max_retries={budget}")
    ma_fail_fast = fabro_runtime.probe_max_attempts(f"retry={budget}")
    assert ma_bounded > 1, (
        "the committed classify budget must yield max_attempts > 1 at the REAL "
        f"fabro runtime (bounded retry effort before exhaustion); got {ma_bounded}"
    )
    assert ma_fail_fast == 1, (
        "NEGATIVE CONTROL: the pre-fix bare `retry=N` must yield max_attempts == 1"
        " — the first-error fail-fast this boundary excludes; got "
        f"{ma_fail_fast}"
    )


# ===========================================================================
# DIAGNOSTIC-LINKAGE leg — reference lead-01jw.3 BY VALUE: the persistent-429
# terminal report IS the existing infra-path / rate-limit-429 diagnostic, and
# the bounded-budget nodes route to that diagnostic on exhaustion.
# ===========================================================================

def test_persistent_rate_limit_terminal_report_is_the_existing_diagnostic():
    """Running the SHIPPED emit_blk ``classify_reason`` (lead-01jw.3) over the
    persistent-429 fault pinned by 738f35759127fe7f derives exactly
    (infra-path, rate-limit-429) — the terminal report on a PERSISTENT rate-limit
    is the EXISTING diagnostic, referenced by value (this runs lead-01jw.3's own
    shipped mechanism; it does not re-pin it).
    """
    emit_blk_body = _node_body(_workflow_text(), "emit_blk")
    reason_class, detail_marker = _derive(
        emit_blk_body, "", _persistent_429_fault()
    )
    assert (reason_class, detail_marker) == _RATE_LIMIT_EXAMPLE_ROW, (
        "the shipped classify_reason must map a PERSISTENT 429 rate-limit failure "
        f"to (infra-path, rate-limit-429); got ({reason_class!r}, {detail_marker!r})"
    )


def test_bounded_budget_nodes_route_to_the_diagnostic_on_exhaustion():
    """The LINKAGE: the LLM/ACP judgment nodes that carry the bounded retry
    budget route ``-> emit_blk [condition="outcome=failed"]`` on retry
    EXHAUSTION — so a node whose transient 429 PERSISTS past its budget reaches
    the rate-limit-429 diagnostic at the END of its bounded effort, not on the
    first error.
    """
    graph = _workflow_text()
    missing_budget = []
    missing_edge = []
    for node in _BOUNDED_BUDGET_DIAGNOSTIC_NODES:
        body = _node_body(graph, node)
        if not (_max_retries(body) or 0) >= 1:
            missing_budget.append(node)
        if not _routes_to_emit_blk_on_failure(graph, node):
            missing_edge.append(node)
    assert not missing_budget, (
        "these nodes lack the EFFECTIVE bounded retry budget that makes "
        f"exhaustion follow a bounded effort: {missing_budget!r}"
    )
    assert not missing_edge, (
        "these bounded-budget nodes do not route to the emit_blk diagnostic on "
        f"retry exhaustion (outcome=failed): {missing_edge!r} — a persistent-429 "
        "exhaustion would not reach the rate-limit-429 diagnostic"
    )


# ===========================================================================
# REFERENCE-NOT-REPIN guard — this behavior references the lead-01jw.3 diagnostic
# scenarios by value; it must NOT duplicate/re-pin them into this feature file.
# ===========================================================================

def test_references_the_diagnostic_scenarios_by_value_does_not_repin_them():
    """The referenced lead-01jw.3 diagnostic scenarios (738f35759127fe7f /
    629be1e0224f3a03) stay in the failsafe-block-diagnostic feature ONLY — this
    behavior does not duplicate/re-pin them here — and this behavior's own
    scenario TEXT references them by value (names their hashes).
    """
    my_feature = _FEATURE.read_text(encoding="utf-8")
    diag_feature = _DIAGNOSTIC_FEATURE.read_text(encoding="utf-8")
    # a RE-PIN is the hash on its own @scenario_hash TAG LINE (line-start after
    # indent); an inline mention inside a step is the BY-VALUE reference we WANT.
    my_tag_hashes = {
        m.group(1)
        for line in my_feature.splitlines()
        if line.lstrip().startswith("@scenario_hash:")
        for m in [re.match(r"@scenario_hash:([0-9a-f]+)", line.lstrip())]
        if m
    }
    for h in _REFERENCED_DIAGNOSTIC_HASHES:
        assert h not in my_tag_hashes, (
            f"diagnostic scenario {h} must NOT be re-pinned as a tag in "
            f"{_FEATURE.name}; it is referenced by value (inline), not duplicated"
        )
        assert h in diag_feature, (
            f"referenced diagnostic scenario {h} must still live in "
            f"{_DIAGNOSTIC_FEATURE.name}"
        )
    # this behavior's scenario references those diagnostic pins BY VALUE.
    from tests.test_lead_6ev8_fabro_llm_transient_retry import _scenario_blocks

    block = _scenario_blocks(my_feature).get(_BEHAVIOR_4_HASH, "")
    assert block, f"behavior-4 scenario {_BEHAVIOR_4_HASH} not found in feature"
    for h in _REFERENCED_DIAGNOSTIC_HASHES:
        assert h in block, (
            f"the behavior-4 scenario must reference diagnostic pin {h} BY VALUE"
        )
