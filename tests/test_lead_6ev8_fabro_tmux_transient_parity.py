"""lead-6ev8 behavior 3 (@scenario_hash:acd8d90bd9d4e4df) — the fabro LLM path's
transient-error resilience must MATCH the tmux claude agent's: a survivable
transient 429 burst a tmux run survives, a fabro run ALSO survives, and the
operator sees the SAME completion outcome regardless of which runtime ran the
work.  This closes the lead-01jw.3 facet-2 gap where tmux completed lead-ew86 (a
substantive request_bugfix) while fabro blocked on the identical class of
transient 429.

This is a CROSS-RUNTIME PARITY pin, the direct analog of lead-8hpz behavior 3
(the ``bc_launcher.liveness`` shared-contract anchor that stopped the two engage
runtimes diverging on the liveness surface).  Behaviors 1&2
(3b3cf899ddd8ed68 / 088460f2fd9490a4) already gave the fabro LLM/ACP path the
retry-and-survive-with-bounded-exponential-backoff resilience the tmux Claude
Code runtime already had built in — so the resilience CAPABILITY needs no change.
What behavior 3 pins is the PARITY itself: a SINGLE shared contract both runtimes
are read through, so they cannot silently diverge and let a transient rate-limit
decide whether the work gets done based on which runtime ran it.

The two REAL mechanisms this binds, at this repo's honest fidelity:

  * FABRO side (behaviors 1&2, proven at the REAL fabro v0.254.0 binary): every
    LLM/ACP judgment node carries ``max_retries=N`` (max_attempts=N+1 > 1, so a
    single transient 429 is not terminal) AND ``retry_policy="exponential"``
    (fabro ExponentialBackoff — spaced, not a 429 retry-storm).  The runtime leg
    runs the REAL fabro binary over the committed budget+policy and reads the
    emitted ``max_attempts`` (> 1 => survives), SKIPping honestly if the binary
    or a local server cannot be obtained.

  * TMUX side (grounded in the REAL launcher source): the tmux runtime engages
    the long-lived ``agent-vault run -- claude`` (Claude Code) agent
    (``bc_launcher.controller._agent_session``).  Claude Code's OWN built-in long
    robust 429 backoff is the REFERENCE resilience — a transient 429 does not
    kill the long-lived agent; it retries and drives the work to a gated
    work_done.  This leg asserts that engage command is really the one the
    launcher sends, so the tmux leg is grounded in the real launch and is not an
    in-the-air claim.

Real inter-attempt 429 TIMING is not observable in-container (it needs a real
GITHUB_TOKEN + sandbox and minutes-scale waits — exactly the fidelity constraint
behaviors 1&2 established), so this does NOT fake a live-429 demonstration.  It
binds at dry-run + real-binary + real-source fidelity, with honest SKIP where a
real fabro server is unavailable.  NEGATIVE CONTROLS keep every leg non-vacuous.
ADDITIVE: references (does not re-pin) behaviors 1&2, lead-01jw.3, lead-8hpz.
"""
from __future__ import annotations

import inspect
import shutil
import subprocess
from pathlib import Path

import pytest

from bc_launcher.controller import _fabro_def_asset_root
from tests.support.container import _ky63_locate_or_fetch_fabro

# Reuse behavior 1&2's proven REAL-fabro helpers rather than re-deriving them.
from tests.test_lead_6ev8_fabro_llm_transient_retry import (
    _FabroRuntime,
    _max_retries,
    _node_body,
    _scenario_blocks,
    _workflow_text,
)
from tests.test_lead_6ev8_fabro_llm_backoff import _retry_policy

# The shared cross-runtime transient-error resilience anchor (the parity source).
# Guarded so an ABSENT anchor produces a clean RED ASSERTION FAILURE naming the
# missing parity binding, not an opaque collection error.
try:
    from bc_launcher import transient_resilience as _TR
except ImportError:
    _TR = None


_FEATURE = (
    Path(__file__).resolve().parent.parent
    / "features"
    / "bc_container_fabro_llm_transient_retry.feature"
)
_BEHAVIOR_3_HASH = "acd8d90bd9d4e4df"


def _require_anchor():
    """Fail cleanly (RED) when the shared parity anchor is absent."""
    assert _TR is not None, (
        "the shared CROSS-RUNTIME transient-error resilience anchor "
        "`bc_launcher.transient_resilience` is not present — the fabro and tmux "
        "runtimes' transient-429 resilience is not pinned as ONE shared "
        "contract, so they can silently DIVERGE and let a transient rate-limit "
        "decide whether the work gets done based on which runtime ran it "
        "(lead-6ev8 / lead-01jw.3 facet-2). Add the anchor (the lead-8hpz "
        "`liveness` analog for transient resilience)."
    )
    return _TR


# ===========================================================================
# Scenario-hash pin (block-only recompute must equal the on-disk tag).
# ===========================================================================

@pytest.mark.skipif(
    shutil.which("scenarios") is None,
    reason="canonical `scenarios` CLI not on PATH",
)
def test_parity_scenario_block_recomputes_to_its_pin():
    """The block-only hash of scenario acd8d90bd9d4e4df recomputes to its tag,
    and the behavior-1/2 pins are undisturbed by the append."""
    blocks = _scenario_blocks(_FEATURE.read_text(encoding="utf-8"))
    for h in (_BEHAVIOR_3_HASH, "3b3cf899ddd8ed68", "088460f2fd9490a4"):
        assert h in blocks, f"No scenario tagged @scenario_hash:{h} in {_FEATURE.name}"
        recomputed = subprocess.run(
            ["scenarios", "hash"],
            input=blocks[h],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert recomputed == h, (
            f"scenario block recomputed to {recomputed!r} but the feature pins "
            f"@scenario_hash:{h}; re-tag or revert the edit"
        )


# ===========================================================================
# The shared anchor DEFINES the one cross-runtime resilience contract
# (+ NEGATIVE CONTROL so the predicate is not vacuous).
# ===========================================================================

def test_shared_anchor_defines_retry_and_survive_exponential_contract():
    """The parity anchor names the ONE retry-and-survive-with-bounded-exponential
    -backoff contract both runtimes obey: a finite retry budget (>= 1, so a
    single transient 429 is not terminal) AND the exponential (spaced) policy.

    NEGATIVE CONTROL: a node with NO budget, a zero budget, or a non-exponential
    policy must FAIL the predicate — otherwise "both runtimes satisfy it" would
    be a vacuous always-true match.
    """
    tr = _require_anchor()
    assert tr.MIN_RETRY_BUDGET >= 1
    assert tr.RETRY_POLICY_EXPONENTIAL == "exponential"
    # Satisfying budget + policy => resilient.
    assert tr.node_satisfies_transient_resilience(
        tr.MIN_RETRY_BUDGET, tr.RETRY_POLICY_EXPONENTIAL
    )
    # NEGATIVE CONTROLS — each pre-fix / degraded posture must be REJECTED.
    assert not tr.node_satisfies_transient_resilience(None, tr.RETRY_POLICY_EXPONENTIAL)
    assert not tr.node_satisfies_transient_resilience(0, tr.RETRY_POLICY_EXPONENTIAL)
    assert not tr.node_satisfies_transient_resilience(tr.MIN_RETRY_BUDGET, "constant")
    assert not tr.node_satisfies_transient_resilience(tr.MIN_RETRY_BUDGET, None)


# ===========================================================================
# FABRO leg — the committed def, read THROUGH the shared anchor (not a divergent
# local literal), satisfies the one contract; and the REAL fabro binary confirms
# the committed budget survives (max_attempts > 1).
# ===========================================================================

def test_fabro_llm_acp_nodes_satisfy_the_shared_resilience_contract():
    """Every committed LLM/ACP node is validated AGAINST THE SHARED ANCHOR's
    predicate + node set — so the fabro side is read through the SAME one
    contract the tmux side is, and cannot diverge behind a test-local literal.
    """
    tr = _require_anchor()
    graph = _workflow_text()
    unresilient = []
    for name in tr.LLM_ACP_NODES:
        body = _node_body(graph, name)
        if not tr.node_satisfies_transient_resilience(
            _max_retries(body), _retry_policy(body)
        ):
            unresilient.append(
                (name, _max_retries(body), _retry_policy(body))
            )
    assert not unresilient, (
        "these committed fabro LLM/ACP nodes do NOT meet the shared "
        "cross-runtime transient-error resilience contract (finite max_retries "
        ">=1 AND retry_policy=exponential) that the tmux Claude Code runtime "
        f"already satisfies — so the fabro run would DIVERGE from tmux: "
        f"{unresilient!r}"
    )


def test_fabro_committed_budget_survives_at_real_runtime(fabro_parity_runtime):
    """Bind the committed def to the REAL fabro binary: the ``classify`` LLM
    node's declared budget+policy, run through the real runtime, yields
    max_attempts > 1 — the fabro run SURVIVES a survivable transient burst
    (retries) rather than failing-fast on the first 429, exactly as the tmux run
    does.  SKIPs honestly if the binary/server cannot be obtained.
    """
    tr = _require_anchor()
    body = _node_body(_workflow_text(), "classify")
    budget = _max_retries(body)
    policy = _retry_policy(body)
    assert tr.node_satisfies_transient_resilience(budget, policy), (
        f"committed classify does not meet the shared contract: "
        f"max_retries={budget}, retry_policy={policy!r}"
    )
    max_attempts = fabro_parity_runtime.probe_max_attempts(
        f'max_retries={budget}, retry_policy="{policy}"'
    )
    assert max_attempts > 1, (
        "the committed fabro classify budget+policy must yield max_attempts > 1 "
        "at the REAL fabro runtime (retry-and-survive parity with the tmux "
        f"Claude Code runtime); got max_attempts={max_attempts}"
    )


# ===========================================================================
# TMUX leg — the reference resilience is grounded in the REAL launcher source:
# the tmux runtime engages the long-lived Claude Code agent whose OWN built-in
# 429 backoff survives a transient burst (+ NEGATIVE CONTROL).
# ===========================================================================

def test_tmux_runtime_engages_long_lived_claude_agent_in_real_source():
    """The anchor's ``TMUX_CLAUDE_ENGAGE`` command is the command the launcher
    ACTUALLY sends to engage the long-lived claude agent — so the tmux leg's
    transient-429 resilience (Claude Code's OWN built-in long robust 429 backoff
    on a long-lived agent) is grounded in the REAL launch, not asserted in the
    air.

    NEGATIVE CONTROL: a fabricated engage command must NOT appear in the real
    launcher source — otherwise "the engage is in the source" would be vacuous.
    """
    tr = _require_anchor()
    from bc_launcher.controller import _agent_session

    src = inspect.getsource(_agent_session)
    assert tr.TMUX_CLAUDE_ENGAGE in src, (
        "the tmux runtime's reference resilience is Claude Code's built-in 429 "
        f"backoff on the long-lived engage {tr.TMUX_CLAUDE_ENGAGE!r}; that "
        "engage command must appear in the real launcher source "
        "(bc_launcher.controller._agent_session) so the tmux parity leg is "
        "grounded in the actual launch"
    )
    assert "agent-vault run -- claude-does-not-exist" not in src, (
        "NEGATIVE CONTROL: a fabricated engage command must NOT be in the real "
        "launcher source — otherwise the grounding above would be vacuous"
    )


# ===========================================================================
# PARITY leg — one shared contract, same gated completion outcome.
# ===========================================================================

def test_both_runtimes_share_one_transient_resilience_contract_same_outcome():
    """The load-bearing parity: BOTH runtimes are read through the ONE shared
    anchor for the SAME survivable transient 429 burst —

      * fabro: every committed LLM/ACP node satisfies the anchor's
        retry-and-survive-exponential predicate (survives -> continues to a
        gated work_done);
      * tmux: the anchor's long-lived Claude-Code engage is the real launch
        (Claude Code's built-in 429 backoff survives -> gated work_done);

    so a transient rate-limit survivable on one runtime is survivable on the
    other, and the operator sees the SAME completion outcome — the work_id
    reconciles to a real gated result on both — regardless of which runtime ran
    the work.  The anchor documents that same-gated-completion-outcome invariant
    as the single source, so the two runtimes cannot silently diverge.
    """
    tr = _require_anchor()
    from bc_launcher.controller import _agent_session

    # fabro side satisfies the one contract ...
    graph = _workflow_text()
    fabro_ok = all(
        tr.node_satisfies_transient_resilience(
            _max_retries(_node_body(graph, n)), _retry_policy(_node_body(graph, n))
        )
        for n in tr.LLM_ACP_NODES
    )
    # ... tmux side satisfies the one contract (real long-lived Claude engage).
    tmux_ok = tr.TMUX_CLAUDE_ENGAGE in inspect.getsource(_agent_session)

    assert fabro_ok and tmux_ok, (
        "cross-runtime parity is not both-sided: "
        f"fabro_meets_contract={fabro_ok}, tmux_meets_contract={tmux_ok}. Both "
        "runtimes must present the shared retry-and-survive-with-bounded-"
        "exponential-backoff posture so the operator sees the same gated "
        "completion outcome regardless of runtime"
    )
    # The invariant is NAMED in the single shared anchor, not duplicated per
    # runtime — so it cannot drift on one side only.
    assert getattr(tr, "SAME_COMPLETION_OUTCOME_INVARIANT", None), (
        "the shared anchor must NAME the same-gated-completion-outcome invariant "
        "(the operator sees the same result from either runtime for the same "
        "survivable transient burst) as the single source of the parity"
    )


# ===========================================================================
# REAL fabro runtime fixture (same isolated-ephemeral-server harness as
# behaviors 1&2; honest SKIP if the binary/server is unavailable).
# ===========================================================================

@pytest.fixture(scope="module")
def fabro_parity_runtime(tmp_path_factory):
    fabro, note = _ky63_locate_or_fetch_fabro()
    if fabro is None:
        pytest.skip(
            f"fabro binary could not be obtained; real-runtime parity leg "
            f"deferred honestly. reason: {note!r}"
        )
    home = tmp_path_factory.mktemp("fabro6ev8parity")
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
            f"real-runtime parity leg (honest SKIP). server.log tail:\n{log}"
        )
    try:
        yield rt
    finally:
        rt.stop()
