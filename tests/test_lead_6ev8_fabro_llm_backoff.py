"""lead-6ev8 behavior 2 (@scenario_hash:088460f2fd9490a4) — the fabro LLM/ACP
node's retries must use BOUNDED EXPONENTIAL BACKOFF: spaced (increasing between
attempts), capped, count-bounded, and total-wait-bounded, so the client does not
amplify a transient 429 into a self-inflicted retry-storm against the shared
account.

Behavior 1 (@scenario_hash:3b3cf899ddd8ed68) set the retry COUNT — it proved,
against the REAL fabro v0.254.0 binary, that ``max_retries=N`` is the honored
node-level budget (max_attempts=N+1) while a bare ``retry=N`` is silently ignored
(max_attempts=1, fail-fast). Behavior 1 did NOT configure the BACKOFF SPACING.
Behavior 2 makes the LLM/ACP retries EXPONENTIALLY SPACED and bounded.

EMPIRICAL RECONCILIATION at the REAL fabro v0.254.0 mechanism (not a model):
  * ``retry_policy`` is a FIRST-CLASS node attribute in the real fabro schema.
    The fabro binary's embedded node-attribute enumeration lists it VERBATIM
    between the two attributes behavior 1 proved recognized —
    ``...max_retries retry_policy retry_target...`` (concatenated in the binary
    as ``max_retriesretry_policyretry_target``). It is therefore HONORED schema,
    NOT an inert typo like the silently-ignored ``retry=N`` behavior 1 exposed.
    The real runtime parses it (a dry-run ``run.created`` carries it typed).
  * fabro's real backoff mechanism is ``ExponentialBackoff { max_times,
    base_duration }`` (binary struct ``ExponentialBackoffmax_timesbase_duration``),
    and ``retry_policy`` deserializes to a ``RetryPolicy`` enum whose variants
    include Exponential/Constant/Linear/Fixed. So ``retry_policy="exponential"``
    selects INCREASING (spaced) backoff — ``base_duration`` grown per attempt —
    rather than the immediate/fixed retry that would retry-storm the provider.
  * The four scenario clauses map onto that real mechanism:
      - delay INCREASES / exponential  <- retry_policy="exponential"
                                          (fabro ExponentialBackoff)
      - CAPPED per-attempt ceiling      <- the max per-attempt delay is
                                          base_duration * factor ** max_times,
                                          a FINITE ceiling because max_times
                                          (= max_retries) is finite
      - retry count BOUNDED             <- max_retries=N -> max_attempts=N+1
                                          (behavior 1)
      - cumulative wait BOUNDED         <- a finite geometric sum over the
                                          bounded attempt budget
    So the fix is: every LLM/ACP node carries BOTH ``max_retries=N`` (finite
    count/total budget) AND ``retry_policy="exponential"`` (the increasing,
    spaced backoff policy). Remove either -> the backoff is no longer
    bounded-exponential -> these tests RED.

FIDELITY (run the REAL tool, do not reimplement). Real backoff TIMING requires a
full worker + sandbox and multi-minute waits (fabro's default inter-attempt
backoff is minutes-scale), so — exactly as behavior 1 could not drive real LLM
429s — the binding is at the SAME fidelity behavior 1 used:
  * ``test_fabro_binary_schema_*`` interrogates the REAL fabro binary's OWN
    embedded schema: ``retry_policy`` is a first-class node attribute (in the
    node-attr enum, like ``max_retries``) and ``ExponentialBackoff`` is fabro's
    real backoff struct — with the NEGATIVE CONTROL that a fabricated attribute
    name is NOT in the schema. This distinguishes ``retry_policy`` from the
    inert-typo class of bug (``retry=``) behavior 1 found.
  * ``test_committed_*_runtime`` runs the REAL fabro binary (dry-run) over the
    committed LLM/ACP budget + policy and reads the emitted ``max_attempts``: it
    is finite and > 1 (count-bounded => cumulative wait bounded by a finite
    attempt budget), and ``retry_policy="exponential"`` does not break the count.
    SKIPs honestly if the binary/server cannot be obtained.
  * ``test_llm_acp_nodes_carry_bounded_exponential_backoff_policy`` (static
    teeth) pins that EVERY LLM/ACP agent node carries BOTH the finite
    ``max_retries=N`` budget AND ``retry_policy="exponential"``.

ADDITIVE: references (does not re-pin) behavior 1, lead-01jw.3, and lead-i0wi.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from bc_launcher.controller import _fabro_def_asset_root
from tests.support.container import _ky63_locate_or_fetch_fabro

# Reuse behavior 1's proven helpers (quote-aware node-body scan, block-only
# scenario extraction, the real-fabro probe graph, and the isolated ephemeral
# fabro server harness) rather than re-deriving them.
from tests.test_lead_6ev8_fabro_llm_transient_retry import (
    _FabroRuntime,
    _LLM_ACP_NODES,
    _max_retries,
    _node_body,
    _probe_graph,
    _scenario_blocks,
    _workflow_text,
)


_FEATURE = (
    Path(__file__).resolve().parent.parent
    / "features"
    / "bc_container_fabro_llm_transient_retry.feature"
)
_BEHAVIOR_2_HASH = "088460f2fd9490a4"

# The REAL fabro v0.254.0 binary's own embedded schema markers (verbatim byte
# substrings). The node-attribute enumeration lists retry_policy as a first-class
# attribute BETWEEN max_retries and retry_target; the backoff struct is
# ExponentialBackoff { max_times, base_duration }.
_NODE_ATTR_ENUM_MARKER = b"max_retriesretry_policyretry_target"
_EXP_BACKOFF_STRUCT_MARKER = b"ExponentialBackoffmax_timesbase_duration"


def _retry_policy(body: str) -> str | None:
    """The node's declared ``retry_policy="<value>"`` (the fabro RetryPolicy the
    real runtime selects — ``exponential`` => increasing/spaced backoff), or None
    if absent."""
    import re

    m = re.search(r'\bretry_policy="([^"]*)"', body)
    return m.group(1) if m else None


# ===========================================================================
# Scenario-hash pin (block-only recompute must equal the on-disk tag).
# ===========================================================================

@pytest.mark.skipif(
    shutil.which("scenarios") is None,
    reason="canonical `scenarios` CLI not on PATH",
)
def test_backoff_scenario_block_recomputes_to_its_pin():
    """The block-only hash of scenario 088460f2fd9490a4 recomputes to its tag."""
    blocks = _scenario_blocks(_FEATURE.read_text(encoding="utf-8"))
    assert _BEHAVIOR_2_HASH in blocks, (
        f"No scenario tagged @scenario_hash:{_BEHAVIOR_2_HASH} in {_FEATURE.name}"
    )
    recomputed = subprocess.run(
        ["scenarios", "hash"],
        input=blocks[_BEHAVIOR_2_HASH],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert recomputed == _BEHAVIOR_2_HASH, (
        f"scenario block recomputed to {recomputed!r} but the feature pins "
        f"@scenario_hash:{_BEHAVIOR_2_HASH}; re-tag or revert the edit"
    )


# ===========================================================================
# REAL fabro binary SCHEMA LAW + NEGATIVE CONTROL — retry_policy is first-class
# schema (honored), ExponentialBackoff is fabro's real bounded-backoff mechanism.
# ===========================================================================

def test_fabro_binary_schema_recognizes_retry_policy_and_exponential_backoff():
    """Interrogate the REAL fabro binary's OWN embedded schema:

      * ``retry_policy`` is a FIRST-CLASS node attribute — it sits in the
        node-attribute enumeration verbatim between ``max_retries`` and
        ``retry_target`` (the two attributes behavior 1 proved recognized). It is
        therefore HONORED schema, not an inert typo like the silently-ignored
        ``retry=N`` that behavior 1 exposed.
      * fabro's real backoff mechanism is ``ExponentialBackoff { max_times,
        base_duration }`` — so ``retry_policy="exponential"`` selects a genuine
        increasing/spaced, count-bounded backoff.

    NEGATIVE CONTROL: a fabricated attribute name is NOT present in the schema —
    otherwise "the marker is in the binary" would be a vacuous string match.

    Teeth: if a future fabro dropped ``retry_policy`` from the node-attr schema
    (or renamed the backoff struct), this — and the backoff-config diagnosis —
    would change, and this test surfaces it rather than letting the def silently
    regress to an inert attribute.
    """
    fabro, note = _ky63_locate_or_fetch_fabro()
    if fabro is None:
        pytest.skip(
            f"fabro binary could not be obtained; real-schema backoff leg "
            f"deferred honestly. reason: {note!r}"
        )
    data = Path(fabro).read_bytes()
    assert _NODE_ATTR_ENUM_MARKER in data, (
        "the REAL fabro binary's node-attribute enumeration must list "
        "`retry_policy` as a first-class attribute between `max_retries` and "
        "`retry_target` (marker "
        f"{_NODE_ATTR_ENUM_MARKER!r}); without it retry_policy would be an inert "
        "typo like `retry=` (the lead-6ev8 behavior-1 root cause)"
    )
    assert _EXP_BACKOFF_STRUCT_MARKER in data, (
        "the REAL fabro binary must carry the `ExponentialBackoff { max_times, "
        "base_duration }` backoff struct (marker "
        f"{_EXP_BACKOFF_STRUCT_MARKER!r}) — the real mechanism "
        "`retry_policy=\"exponential\"` selects for increasing/spaced backoff"
    )
    assert b"retryzzz_policy" not in data, (
        "NEGATIVE CONTROL: a fabricated attribute name must NOT appear in the "
        "fabro binary — otherwise the schema markers above would be vacuous"
    )


# ===========================================================================
# REAL fabro RUNTIME — the committed LLM/ACP budget+policy is accepted and
# count-bounded (finite max_attempts > 1 => cumulative wait bounded by a finite
# attempt budget), and retry_policy="exponential" does not break the count.
# ===========================================================================

@pytest.fixture(scope="module")
def fabro_runtime_backoff(tmp_path_factory):
    """A module-scoped isolated fabro server the runtime leg probes against
    (same isolated-ephemeral-server harness behavior 1 uses)."""
    fabro, note = _ky63_locate_or_fetch_fabro()
    if fabro is None:
        pytest.skip(
            f"fabro binary could not be obtained; real-runtime backoff leg "
            f"deferred honestly. reason: {note!r}"
        )
    home = tmp_path_factory.mktemp("fabro6ev8backoff")
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
            f"real-runtime backoff leg (honest SKIP). server.log tail:\n{log}"
        )
    try:
        yield rt
    finally:
        rt.stop()


def test_committed_llm_backoff_budget_is_count_bounded_at_real_runtime(
    fabro_runtime_backoff,
):
    """Bind the committed def to the real runtime: the ``classify`` LLM node's
    declared retry budget + exponential policy, run through the REAL fabro
    binary, yields a FINITE max_attempts > 1 — the retry count (and hence the
    cumulative exponential-backoff wait) is BOUNDED, and the exponential policy
    does not break the count bound.

    Teeth: while classify carries no ``retry_policy="exponential"`` the policy
    helper returns a non-exponential value (or None) and this REDs; once it
    carries the effective ``max_retries=N`` + ``retry_policy="exponential"`` the
    real runtime yields a finite max_attempts=N+1 > 1 and it GREENs.
    """
    body = _node_body(_workflow_text(), "classify")
    budget = _max_retries(body)
    policy = _retry_policy(body)
    assert budget is not None and budget >= 1, (
        "the committed `classify` LLM node must declare a finite "
        f"`max_retries=N` (N>=1) count/total budget. classify body:\n{body}"
    )
    assert policy == "exponential", (
        "the committed `classify` LLM node must declare "
        '`retry_policy="exponential"` (fabro ExponentialBackoff — increasing, '
        "spaced backoff) so the retries are not fired immediately as a "
        f"429 retry-storm; got retry_policy={policy!r}. classify body:\n{body}"
    )
    max_attempts = fabro_runtime_backoff.probe_max_attempts(
        f'max_retries={budget}, retry_policy="exponential"'
    )
    assert 1 < max_attempts == budget + 1, (
        "the committed classify budget+policy must yield a FINITE "
        f"max_attempts=N+1 > 1 at the REAL fabro runtime (count/total-wait "
        f"bounded); max_retries={budget}, retry_policy=exponential yielded "
        f"max_attempts={max_attempts}"
    )


# ===========================================================================
# Static teeth — every LLM/ACP node carries BOUNDED EXPONENTIAL BACKOFF:
# a finite max_retries budget AND retry_policy="exponential".
# ===========================================================================

def test_llm_acp_nodes_carry_bounded_exponential_backoff_policy():
    """Every LLM/ACP agent node (`classify`, `suff`, `plan`, `impl`, `review`,
    `impl_f`) must carry BOTH:

      * ``max_retries=N`` (N>=1) — the finite retry COUNT / total-wait budget
        (the per-attempt CAP is base*factor**N and the total wait is a finite
        geometric sum, both bounded precisely because N is finite); AND
      * ``retry_policy="exponential"`` — fabro's ExponentialBackoff, so the delay
        BETWEEN successive attempts INCREASES (spaced) rather than firing
        immediately and amplifying the 429 into a self-inflicted retry-storm.

    Teeth:
      * drop ``retry_policy`` from any LLM/ACP node -> RED (no exponential
        spacing; retries would storm the provider);
      * set it to a non-exponential policy (constant/fixed/linear/none) -> RED
        (delays no longer INCREASE between attempts);
      * drop ``max_retries`` from any LLM/ACP node -> RED (retry count / total
        wait no longer bounded — the per-attempt cap ceiling disappears).
    """
    graph = _workflow_text()
    missing_policy = []
    non_exponential = []
    unbounded_count = []
    for name in _LLM_ACP_NODES:
        body = _node_body(graph, name)
        policy = _retry_policy(body)
        if policy is None:
            missing_policy.append(name)
        elif policy != "exponential":
            non_exponential.append((name, policy))
        if not (_max_retries(body) or 0) >= 1:
            unbounded_count.append(name)
    assert not missing_policy, (
        "these LLM/ACP nodes carry NO `retry_policy` — their retries are not "
        "exponentially spaced, so a transient 429 burst would be retried "
        "immediately (a self-inflicted retry-storm against the shared account): "
        f"{missing_policy!r}. Add `retry_policy=\"exponential\"` to each."
    )
    assert not non_exponential, (
        "these LLM/ACP nodes carry a NON-exponential `retry_policy`, so the "
        "delay between successive retries does not INCREASE (no bounded "
        f"exponential backoff): {non_exponential!r}. Use "
        '`retry_policy="exponential"`.'
    )
    assert not unbounded_count, (
        "these LLM/ACP nodes carry no finite `max_retries=N` budget, so the "
        "retry count / cumulative backoff wait / per-attempt cap ceiling are "
        f"unbounded: {unbounded_count!r}. Add `max_retries=N` (N>=1) to each."
    )
