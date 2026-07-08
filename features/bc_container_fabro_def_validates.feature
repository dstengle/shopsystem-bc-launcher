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

  @scenario_hash:a5e16a192f755768 @bc:shopsystem-bc-launcher
  Scenario: the poured dispatcher def is a native cyclic poll-loop with poll, dispatch and wait as native script nodes, a back-edge from wait to poll, no long-running watch node, and no LLM node in the loop
    Given the shopsystem-bc-launcher BC is installed
    And the container "bc-shopsystem-messaging" is running with the self-contained fabro def set POURED by shop-templates into "/workspace/.fabro/", including the "dispatcher.fabro" graph def the "dispatcher.toml" entrypoint applies
    When the poured "dispatcher.fabro" def is inspected structurally, without a live docker daemon, a running fabro server, or a reachable agent-vault
    Then the "dispatcher.fabro" is a CYCLIC graph whose loop is "start -> poll -> dispatch -> wait -> poll", the "wait -> poll" edge being the BACK-EDGE that forms the cycle, so the run persists by cycling poll->dispatch->wait->poll rather than blocking on a single long-running watch
    And the "poll" node is a NATIVE "script=" node with no LLM that lists the current pending inbox via "shop-msg pending inbox --bc shopsystem-messaging" and yields the concrete pending work ids, returning promptly rather than blocking
    And the "dispatch" node is a NATIVE "script=" node with no LLM that acts on the pending work ids from "poll"
    And the "wait" node is a NATIVE "script=" node with no LLM that sleeps a short interval before the back-edge returns to "poll"
    And the def contains NO long-running "shop-msg watch" node and NO LLM/agent node (no Haiku "launch" node and no other model-backed node) anywhere in the loop, so the steady-state loop consumes NO model tokens and tokens are spent only on the child's actual work

  @scenario_hash:6088da7e9e4c4e59 @bc:shopsystem-bc-launcher
  Scenario: the native dispatch node hands each pending work_id to its child via a per-child "[run.environment.env] WORK_ID" overlay and spawns it detached, with a negative control that "-I WORK_ID" does not reach the child's native script env
    Given the shopsystem-bc-launcher BC is installed
    And the container "bc-shopsystem-messaging" is running with the self-contained fabro def set POURED by shop-templates into "/workspace/.fabro/", including the "dispatcher.fabro" graph def and the UNCHANGED ADR-051 child def
    And the "poll" node has yielded a concrete pending work id "W" from "shop-msg pending inbox --bc shopsystem-messaging"
    When the poured "dispatcher.fabro" def's native "dispatch" node script and the per-child ".toml" it materializes are inspected structurally, without a live docker daemon, a running fabro server, or a reachable agent-vault
    Then for each pending work id "W" the native "dispatch" node materializes a per-child ".toml" that carries the CONCRETE work id in a "[run.environment.env]" overlay as "WORK_ID=W", so the child receives its work id through the child ".toml" env overlay
    And the "dispatch" node then spawns that child DETACHED by issuing "fabro run child.toml --detach", so children run in PARALLEL isolated per WORK_ID and the dispatch node does not block on them before the "wait -> poll" back-edge
    And the spawned child runs the UNCHANGED ADR-051 child def, and the concrete "WORK_ID=W" from the "[run.environment.env]" overlay REACHES that child's native "script=" node env so the child acts on its own work id (BC-proven: a detached child ran with child-ran-WORK_ID delivered via the env overlay)
    And as the negative control, had the dispatch instead passed the work id as "-I WORK_ID=W" (the ADR-058 mechanism), that value would NOT reach the child's native "script=" node env — the exact delivery gap this "[run.environment.env]" overlay exists to close, and the reason no Haiku "launch" node is needed
