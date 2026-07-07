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

  @scenario_hash:bf9f8c9d7f2865e3 @bc:shopsystem-bc-launcher
  Scenario: the fabro engage is ONE persistent cyclic dispatcher whose Haiku launch node fans out a detached child per pending inbox work item, isolated per WORK_ID, surviving child failure and requiring no launch-time work id
    Given the shopsystem-bc-launcher BC is installed
    And bc-container launch is run for BC name "shopsystem-messaging" on the fabro orchestrator launch path selected by "--orchestrator fabro" with no "--work-id" supplied
    And the container "bc-shopsystem-messaging" is running with the self-contained fabro def set POURED by shop-templates into "/workspace/.fabro/" at launch, including "dispatcher.fabro" and the UNCHANGED ADR-051 "workflow.fabro" child def
    And the launcher's idempotent readiness barrier composing the messaging DB and the agent-vault broker has passed (scenario 34)
    When the engage the launcher issues and the poured "dispatcher.fabro" def are inspected structurally, without a live docker daemon, a running fabro server, or a reachable agent-vault
    Then AFTER the readiness barrier passes the launcher issues ONE persistent "fabro run dispatcher.fabro -I BC_NAME=shopsystem-messaging" as the engage, carrying only the constant BC_NAME via "[run.environment.env]" and supplying NO "-I WORK_ID" and requiring NO "--work-id", so that one run is the reactive dispatcher owning the container's lifecycle and discovering work ids at runtime (ADR-058 D1)
    And the poured "dispatcher.fabro" is a CYCLIC graph with exactly one start terminal "start" ("shape=Mdiamond") and exactly one shutdown terminal "end" ("shape=Msquare"), whose edges are "start -> watch", the unconditional "watch -> launch", the conditional "watch -> end" ("condition=outcome=failed"), and the back-edge "launch -> watch" that forms the cycle, so the run persists — cycling watch->launch->watch — until shutdown (ADR-058 D2)
    And the "watch" node is a NATIVE "script=" node with no LLM that on entry FIRST drains "shop-msg pending inbox --bc shopsystem-messaging" non-blockingly and exits 0 immediately when it is non-empty (startup / catch-up drain of messages arrived between sessions or while launch was busy), ELSE blocks on "shop-msg watch --bc shopsystem-messaging", SKIPS the leading "READY" sentinel and exits 0 on the first real event line as a WAKE, and exits nonzero when the watch stream CLOSES, taking the "watch -> end" shutdown edge (ADR-058 D2/D5)
    And the "launch" node is a HAIKU-powered AGENT node pinned to "claude-haiku-4-5" via the graph "model_stylesheet", which reads the AUTHORITATIVE pending set from "shop-msg pending inbox --bc shopsystem-messaging" (the no-matching-outbox, consumption-robust source) and per pending work id W spawns ONE detached child by issuing "fabro run workflow.fabro -I BC_NAME=shopsystem-messaging -I WORK_ID=W --parent <dispatcher-run> --detach", writing the CONCRETE WORK_ID into that child's env overlay because "-I" does not reach the child's native "script=" node env (ADR-058 D3, proof design caveat)
    And each spawned child is the UNCHANGED ADR-051 "workflow.fabro" def running as a distinct parent-linked run isolated by its own per-run WORK_ID, so multiple children run in PARALLEL with no shared work-id file and one child = one work item = one work_done (ADR-058 D3/D4, ADR-051 intact)
    And the launch node does NOT wait on the children and takes the unconditional "launch -> watch" back-edge, so a failed, crashed or bad-dispatch child is isolated to its own detached run and does NOT terminate the dispatcher, which keeps cycling and re-attempts any still-pending work id from the authoritative "pending inbox" source on the next cycle (ADR-058 D5)
    And no tmux "agent" send-keys session and no "claude" engage is started on this path, and the container, credential-proxy, postgres DSN and shop-msg mailbox surfaces are unchanged from the tmux path, only the engage tier differing (ADR-050 D1/D2/D3 launch parity, ADR-058 D6)
