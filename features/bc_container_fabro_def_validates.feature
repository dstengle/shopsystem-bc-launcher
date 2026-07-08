@bc:shopsystem-bc-launcher @origin:adr-051
Feature: a launched bc-base BC carries a self-contained VALID fabro loop def (lead-ky63)

  Companion block-only PIN for the self-contained fabro loop def bundle that
  lead-h2bj delivered under src/bc_launcher/assets/fabro-def/ (the 15-file
  def, launch-wired to place at /workspace/.fabro/). Where lead-h2bj's plain
  unit tests guard the DELIVERY (files present, placement wiring, additive),
  this scenario pins the def's VALIDITY as an ADR-051 Implementer->Reviewer
  loop that `fabro validate` accepts with a placeholder-only native vault.

  FIDELITY (test-fidelity-for-image-layer-container-runtime-scenarios + the
  fabro-asset lesson: run the REAL tool, do not reimplement):
  * LEG 1 (`fabro validate` exits zero + zero diagnostics) runs the REAL
    fabro binary (fabro-sh/fabro v0.254.0, target-triple release asset per
    bead 0fz) against the committed def's workflow.fabro and asserts exit 0
    and an EMPTY diagnostics array (`--json`). If the binary genuinely cannot
    be obtained (no network), the leg SKIPs gracefully and says so honestly;
    it does NOT paper a failure over. A real non-zero / non-empty-diagnostics
    result is a real def defect and REDs.
  * LEG 2 (ADR-051 graph invariants) parses the REAL committed workflow.fabro
    (quote-aware, comment-stripped) and asserts, with teeth: the graph file
    is present; every prompt_file node body it references is present in the
    def; on the scenario success path `emit_r` (the reviewer emitter) is the
    SOLE gated work_done(complete) emitter; and every fallible non-terminal
    node carries an unconditional outcome=failed failsafe edge to a halt or
    blocked-emit sink (no fallible node reaches the SUCCEEDED terminal on
    failure). A missing failsafe edge or a second scenario-path emitter REDs.
  * LEG 3 (native vault) asserts vaults/default/secrets.json holds ONLY
    "__PLACEHOLDER__" for every provider-key/token slot (valid JSON, no
    real-credential-shaped literal). A real value REDs (ADR-049).

  @scenario_hash:2dfefe2ba81e418d
  Scenario: a launched bc-base BC has a self-contained valid fabro loop def that "fabro validate" accepts with the native fabro vault holding only placeholders
    Given the shopsystem-bc-launcher BC is installed
    And bc-container launch is run with BC name "shopsystem-messaging"
    And the container "bc-shopsystem-messaging" is running on the pinned bc-base image
    When "fabro validate" is executed against the fabro def present in that running container
    Then it exits zero and reports zero diagnostics
    And the def is a self-contained bc-shop Implementer->Reviewer loop graph per ADR-051: the graph file is present, every node body the graph references is present in the def alongside it so the loop is runnable from the def alone, the Reviewer node is the sole node that can emit a gated work_done on the success path, and every fallible node carries an explicit unconditional failsafe edge to a halt or blocked-emit sink so a failed node never advances to the SUCCEEDED terminal
    And the def's native fabro vault holds only the value "__PLACEHOLDER__" for each of its provider-key and token slots, with no real credential present in the def (ADR-049), so that any real credential the loop uses is sourced from the agent-vault surface baked in S1 and never from the fabro vault

  @scenario_hash:9f31aa5f378410b1 @bc:shopsystem-bc-launcher
  Scenario: the poured dispatcher def is a cyclic poll-loop with poll and wait as native script nodes and dispatch as the only agent node — a non-LLM ACP script-agent — a back-edge from wait to poll, no long-running watch node, and no LLM node in the loop
    Given the shopsystem-bc-launcher BC is installed
    And the container "bc-shopsystem-messaging" is running with the self-contained fabro def set POURED by shop-templates into "/workspace/.fabro/", including the "dispatcher.fabro" graph def the "dispatcher.toml" entrypoint applies
    When the poured "dispatcher.fabro" def is inspected structurally, without a live docker daemon, a running fabro server, or a reachable agent-vault
    Then the "dispatcher.fabro" is a CYCLIC graph whose loop is "start -> poll -> dispatch -> wait -> poll", the "wait -> poll" edge being the BACK-EDGE that forms the cycle, so the run persists by cycling poll->dispatch->wait->poll rather than blocking on a single long-running watch
    And the "poll" node is a NATIVE "script=" node with no LLM that lists the current pending inbox via "shop-msg pending inbox --bc shopsystem-messaging" and yields the concrete pending work ids, returning promptly rather than blocking
    And the "dispatch" node is the only agent node — a non-LLM ACP script-agent ("backend=acp") — that acts on the pending work ids from "poll"
    And the "wait" node is a NATIVE "script=" node with no LLM that sleeps a short interval before the back-edge returns to "poll"
    And the def contains NO long-running "shop-msg watch" node and its only agent node is the non-LLM ACP dispatch script-agent (no Haiku "launch" node and no other model-backed LLM node) anywhere in the loop, so the steady-state loop consumes NO model tokens and tokens are spent only on the child's actual work

  @scenario_hash:7709a671bdfaddb7 @bc:shopsystem-bc-launcher
  Scenario: the poured dispatcher's dispatch node is an ACP-backed agent node carrying backend="acp" and an acp.command or acp.config attr, wired to receive the poll context and return dispatch decisions, not a native command node
    Given the shopsystem-bc-launcher BC is installed
    And the container "bc-shopsystem-messaging" is running with the self-contained fabro def set POURED by shop-templates into "/workspace/.fabro/", including the "dispatcher.fabro" graph def the "dispatcher.toml" entrypoint applies
    When the poured "dispatcher.fabro" def's "dispatch" node is inspected structurally, without a live docker daemon, a running fabro server, or a reachable agent-vault
    Then the "dispatch" node is an ACP-backed AGENT node carrying "backend=acp" together with an "acp.command" attr (a shell such as "python3 <dispatch_acp_agent.py>") OR an "acp.config" attr (a JSON stdio config), so fabro drives it through the agent-client-protocol backend
    And the "dispatch" node is NOT a native "script="/parallelogram command node, so the pre-fix context-blind command dispatch is absent
    And the "dispatch" node is wired to RECEIVE the incoming context yielded by the "poll" node — the pending inbox work ids plus the in-flight run state — as its input
    And the "dispatch" node is wired to RETURN structured dispatch DECISIONS as its output, which the loop consumes to spawn children, so the dispatch step both reads context and emits decisions rather than blindly re-acting on raw work ids each cycle

  @scenario_hash:713d01c4f4dfd107 @bc:shopsystem-bc-launcher
  Scenario: for a work id whose prior child is still in flight the ACP dispatch node's decision is to SKIP re-dispatch and for a work id with no live child its decision is to SPAWN, with a negative control that the pre-fix native command node re-dispatched every cycle
    Given the shopsystem-bc-launcher BC is installed
    And the container "bc-shopsystem-messaging" is running with the self-contained fabro def set POURED by shop-templates into "/workspace/.fabro/", including the "dispatcher.fabro" graph def whose "dispatch" node is the ACP-backed agent node
    And the incoming context carries a pending work id "W" AND the in-flight run state records that a prior child for "W" is still running and has not yet emitted work_done
    When the ACP-backed "dispatch" node's decision contract is inspected structurally against that context, without a live docker daemon, a running fabro server, or a reachable agent-vault
    Then the decision returned for the still-in-flight work id "W" is to SKIP re-dispatch, so NO second child is spawned for "W" while its prior child is live, and the two children cannot collide on the shared per-"W" git worktree
    And when the in-flight run state records NO live child for a pending work id "V", the decision returned for "V" is to SPAWN a child, so a genuinely unstarted work id is still dispatched exactly once
    And as the negative control, the pre-fix native command "dispatch" node carried NO in-flight skip and re-dispatched every still-pending work id each ~6s cycle — the exact duplicate-spawn the ACP node's in-flight tracking exists to eliminate

  @scenario_hash:f38ab66672151669 @bc:shopsystem-bc-launcher
  Scenario: for each work id the ACP dispatch node decides to spawn, the spawned child receives its concrete WORK_ID via a per-child "[run.environment.env] WORK_ID" overlay carried from the ACP agent's context, preserving the lead-b3f0 delivery guarantee
    Given the shopsystem-bc-launcher BC is installed
    And the container "bc-shopsystem-messaging" is running with the self-contained fabro def set POURED by shop-templates into "/workspace/.fabro/", including the "dispatcher.fabro" graph def whose "dispatch" node is the ACP-backed agent node and the UNCHANGED ADR-051 child def
    And the ACP dispatch node's decision for a pending work id "W" with no live child is to SPAWN a child
    When the ACP-backed "dispatch" node's decision contract and the per-child config it materializes for "W" are inspected structurally, without a live docker daemon, a running fabro server, or a reachable agent-vault
    Then the per-child config the ACP node materializes for "W" carries the CONCRETE work id in a "[run.environment.env]" overlay as "WORK_ID=W", so the child receives its own work id through the child config env overlay
    And the ACP node spawns that child DETACHED, so decided children run in PARALLEL isolated per WORK_ID and the dispatch step does not block on them before the loop's "wait -> poll" back-edge
    And the spawned child runs the UNCHANGED ADR-051 child def, and the concrete "WORK_ID=W" from the "[run.environment.env]" overlay REACHES that child's native "script=" node env so the child acts on its own work id, preserving the lead-b3f0 delivery guarantee under the ACP dispatch

