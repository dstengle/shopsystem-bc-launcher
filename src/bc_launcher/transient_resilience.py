"""The single CROSS-RUNTIME transient-error resilience contract both engage
runtimes obey (lead-6ev8 behavior 3 / @scenario_hash:acd8d90bd9d4e4df).

The launcher engages a BC under one of two runtimes — the tmux/claude
session-start loop (DEFAULT) or the ``--orchestrator fabro`` watcher supervisor.
A transient rate-limit (a 429 burst that resolves within a survivable window)
must NOT decide whether the work gets done based on which runtime ran it: a burst
a tmux run survives, a fabro run must ALSO survive, and the operator must see the
SAME completion outcome from either runtime.  This is the resilience gap
lead-01jw.3 facet-2 recorded — tmux completed lead-ew86 (a substantive
request_bugfix) while fabro BLOCKED opaquely on the identical class of transient
429.  This module is the ONE place that names the shared transient-error
resilience contract, so the two runtimes cannot silently DIVERGE on it — the
direct analog of :mod:`bc_launcher.liveness` (lead-8hpz behavior 3) for the
transient-error-resilience surface.

The two REAL mechanisms this contract governs:

  * TMUX side — Claude Code's OWN built-in long robust 429 backoff.  The tmux
    session-start loop engages the long-lived :data:`TMUX_CLAUDE_ENGAGE` agent
    (``bc_launcher.controller._agent_session``); a transient 429 does not kill
    that long-lived agent — Claude Code retries internally with its own robust
    backoff and drives the work to a real gated work_done.  This is the
    REFERENCE resilience the fabro side must match.

  * FABRO side — the LLM/ACP judgment nodes' declared retry budget + policy.
    Behaviors 1&2 (3b3cf899ddd8ed68 / 088460f2fd9490a4) gave every fabro LLM/ACP
    node BOTH ``max_retries=N`` (the real fabro runtime honors it as
    max_attempts=N+1 > 1, so a single transient 429 is not terminal) AND
    ``retry_policy="exponential"`` (fabro ExponentialBackoff — the delay between
    successive retries INCREASES and is spaced, so the client does not amplify a
    429 into a self-inflicted retry-storm).  That is the SAME
    retry-and-survive-with-bounded-exponential-backoff posture Claude Code's tmux
    runtime already had.

Because both runtimes are read through THIS one contract — the fabro def
validated against :func:`node_satisfies_transient_resilience`, the tmux engage
grounded in :data:`TMUX_CLAUDE_ENGAGE` — a transient burst survivable on one
runtime is survivable on the other, and the work_id reconciles to a real gated
result on BOTH: the SAME completion outcome regardless of runtime
(:data:`SAME_COMPLETION_OUTCOME_INVARIANT`).  The resilience CAPABILITY itself
needed no change here (behaviors 1&2 + Claude Code's built-in backoff already
deliver it); this module PINS the parity so it cannot drift on one side only.
"""
from __future__ import annotations

# The fabro RetryPolicy that selects BOUNDED EXPONENTIAL backoff (fabro's real
# ``ExponentialBackoff`` struct) — the SAME increasing/spaced retry posture
# Claude Code's tmux runtime already uses for transient 429s.  Behaviors 1&2
# placed exactly this policy on every fabro LLM/ACP node.
RETRY_POLICY_EXPONENTIAL = "exponential"

# The minimum finite retry budget a node must declare.  ``max_retries >= 1`` =>
# max_attempts > 1 at the REAL fabro runtime (behavior 1 proved
# max_retries=N -> max_attempts=N+1), so a single transient 429 is NOT terminal —
# matching Claude Code's retry-don't-fail-fast posture.
MIN_RETRY_BUDGET = 1

# The fabro-side LLM/ACP judgment nodes the contract governs — the model-backed
# ``class=`` + ``prompt=`` nodes that are the fabro analog of the tmux claude
# agent's model calls (as opposed to the deterministic native ``script=`` nodes).
LLM_ACP_NODES = ("classify", "suff", "plan", "impl", "review", "impl_f")

# The command the tmux session-start loop sends to engage the long-lived Claude
# Code agent (``bc_launcher.controller._agent_session``).  Claude Code's OWN
# built-in long robust 429 backoff on this long-lived agent IS the tmux-side
# transient-error resilience — the reference the fabro side now matches.
TMUX_CLAUDE_ENGAGE = "agent-vault run -- claude"

# The operator-visible parity invariant, named ONCE here so it cannot drift on
# one runtime only: for the same survivable transient burst the work_id
# reconciles to a real gated work_done on BOTH runtimes — the operator sees the
# SAME completion outcome regardless of which runtime ran the work, so a
# transient rate-limit does not decide whether the work gets done by runtime.
SAME_COMPLETION_OUTCOME_INVARIANT = (
    "for the same survivable transient 429 burst the work_id reconciles to a "
    "real gated work_done on BOTH the tmux/claude and fabro runtimes; the "
    "operator sees the same completion outcome regardless of which runtime ran "
    "the work"
)


def node_satisfies_transient_resilience(max_retries, retry_policy) -> bool:
    """True iff a fabro LLM/ACP node's declared budget + policy meets the shared
    cross-runtime transient-error resilience contract:

      * a FINITE retry budget ``max_retries >= MIN_RETRY_BUDGET`` — so
        max_attempts > 1 and a single transient 429 is not terminal; AND
      * ``retry_policy == RETRY_POLICY_EXPONENTIAL`` — spaced, increasing
        backoff rather than an immediate 429 retry-storm.

    This is the SAME retry-and-survive-with-bounded-exponential-backoff posture
    Claude Code's tmux runtime already has for transient 429s, so a node meeting
    it will not DIVERGE from the tmux run on a survivable burst.
    """
    return (
        max_retries is not None
        and max_retries >= MIN_RETRY_BUDGET
        and retry_policy == RETRY_POLICY_EXPONENTIAL
    )
