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

  @scenario_hash:738f35759127fe7f @bc:shopsystem-bc-launcher
  Scenario Outline: the blocked work_done classifies the failure into the correct reason class and, for an infra-path failure, names the failing infra subsystem, mirroring the tmux-runtime launch-diagnostic cause-marker idiom
    Given the container "bc-shopsystem-messaging" is running the "--orchestrator fabro" watcher engage with its single long-lived shared per-container fabro server
    And an inbound message fires one finite "fabro run workflow.fabro" child whose run fails because <fault>
    When the finite child emits its terminal blocked work_done
    Then the blocked work_done's reason class is exactly "<reason_class>"
    And the blocked work_done additionally names the failing subsystem or gate with the marker token "<detail_marker>" so the operator is pointed at the right repair, exactly as the tmux-runtime launch diagnostic points with its cause-marker token
    And the blocked work_done still carries the failing node identifier and the captured error context alongside that reason class, so the classification never replaces the raw diagnosis

    Examples:
      | fault                                                                       | reason_class    | detail_marker |
      | a deliverable Reviewer gate rejected the produced work                       | deliverable-gate | deliverable   |
      | the anthropic-oauth-shim the fabro anthropic base_url routes through failed  | infra-path       | oauth-shim    |
      | the agent-vault broker the container routes through was unreachable          | infra-path       | agent-vault   |
      | the credential proxy the run routes model calls through refused the request  | infra-path       | proxy         |
      | the model provider returned HTTP 429 rate-limit responses until the run gave up | infra-path    | rate-limit-429 |
      | the LLM produced an unusable or non-advancing response so the node could not proceed | llm-path  | llm-path      |
      | the run failed for a cause the failsafe could not classify                   | unknown          | unknown       |

  @scenario_hash:8af4e27a05ae9a32 @bc:shopsystem-bc-launcher
  Scenario: the fabro block work_done is as actionable as a tmux claude agent's clarify/block, so the operator reconciles or routes from either runtime identically
    Given a tmux-engaged claude BC that fails on the same substantive work emits a detailed clarify/block naming the failing point, the failure class, and the captured context via the shop-msg surface
    And a fabro-engaged BC processing that same substantive work fails and emits its blocked work_done
    When an operator reads each runtime's response from the shop-msg surface to reconcile the work_id and decide how to route it
    Then the fabro blocked work_done exposes the same three decision inputs the tmux clarify/block exposes — the failing point (node), the failure class (reason class), and the captured context — so it is as actionable as the tmux response
    And the operator can reconcile the work_id and route the failure (retry, escalate infra, escalate LLM path, or return the deliverable gate to the PO/Architect) using ONLY the fabro blocked work_done, with no need to attach into the container or read fabro run logs out of band
    And the reconcile-and-route decision the operator reaches is the SAME from the fabro blocked work_done as from the equivalent tmux clarify/block for the same failure, so the diagnostic interface is consistent across the tmux and fabro runtimes

  @scenario_hash:b5bd016991cc2774 @bc:shopsystem-bc-launcher
  Scenario: even the last-resort failsafe path emits reason class unknown WITH the captured run tail and never a content-free block and never a silent complete
    Given the container "bc-shopsystem-messaging" is running the "--orchestrator fabro" watcher engage
    And a finite "fabro run workflow.fabro" child fails at a node whose failure the failsafe cannot classify into deliverable-gate, infra-path, or llm-path
    When the failsafe emits the terminal work_done for that run
    Then the failsafe still emits status "blocked" — never a silent "complete" for a run that did not produce a deliverable
    And the reason class is set to "unknown" rather than left empty, and the failing node identifier and the captured run tail are still attached, so even the unclassified case is diagnosable rather than content-free
    And the failsafe NEVER emits the bare generic summary alone with empty node, empty reason, and empty body, closing the lead-01jw.3 content-free-block regression at the failsafe floor
