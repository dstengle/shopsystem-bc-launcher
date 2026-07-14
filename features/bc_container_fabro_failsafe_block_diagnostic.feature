@bc:shopsystem-bc-launcher
Feature: the fabro finite-run failsafe block report is diagnostic, not content-free
  # lead-01jw.3 (request_bugfix, ADDITIVE). The fabro finite-run failsafe
  # (workflow.fabro `emit_blk`) TODAY reports a block via a NON-consuming
  # `shop-msg nudge` (ADR-051: a de-pending `respond work_done --status blocked`
  # would consume-and-lose the retriable dispatch, so lead-i0wi F2 replaced it
  # with the nudge). But that nudge note is a GENERIC CONTENT-FREE string with an
  # empty failing-node, empty reason, and empty body — the lead-01jw.3 regression
  # first observed as the stale lead-ew86 outbox row (id=1712, scenario_hashes=[],
  # the generic summary). These scenarios pin WHAT the failed finite child's block
  # report SAYS. They COMPLEMENT pin 7a4f7eed52594107 (a failed finite child is
  # NON-FATAL and the watcher keeps serving) — that pins the watcher keeps
  # serving; these pin what the block report carries. ADR-051 is preserved: the
  # block stays a non-consuming report (never a false complete, never a consuming
  # blocked work_done), enriched — not re-authored — to carry the diagnosis.
  #
  # NOTE (behaviors 2–4 append their scenarios BELOW this one, same file):
  #   738f35759127fe7f — reason-class classification + infra subsystem markers
  #   8af4e27a05ae9a32 — cross-runtime parity with a tmux clarify/block
  #   b5bd016991cc2774 — failsafe floor: unknown reason + run tail, never content-free

  @scenario_hash:629be1e0224f3a03 @bc:shopsystem-bc-launcher
  Scenario: a fabro finite run that fails at a workflow.fabro node emits a blocked work_done carrying the failing node, a reason class, and captured error context — not the generic content-free failsafe summary
    Given the container "bc-shopsystem-messaging" is running the "--orchestrator fabro" watcher engage with its single long-lived shared per-container fabro server
    And an inbound message carrying a work_id on a scenario path fires one finite "fabro run workflow.fabro" child
    And that finite child's workflow reaches a node that FAILS, so the run terminates without a deliverable
    When the finite child emits its terminal work_done for that work_id
    Then the emitted work_done has status "blocked" and its body carries the failing NODE identifier — the name of the workflow.fabro node at which the run failed — so the operator knows WHERE the run stopped
    And the blocked work_done carries a REASON CLASS naming which class of failure occurred, drawn from the closed set {deliverable-gate, infra-path, llm-path, unknown}
    And the blocked work_done carries the captured error CONTEXT of the failing node — the run's failing output or tail — so the operator sees WHY it stopped
    And the blocked work_done is NOT the generic content-free failsafe summary "a deliverable-side gate or step failed (see run context); reporting blocked, never a silent complete" with an empty failing-node, empty reason, and empty body, which is the lead-01jw.3 regression this replaces
