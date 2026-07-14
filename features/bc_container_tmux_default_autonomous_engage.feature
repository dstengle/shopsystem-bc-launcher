@bc:shopsystem-bc-launcher @origin:adr-050
Feature: bc-container tmux-DEFAULT engage restores autonomous drain-AND-process — pending dispatches reach a Reviewer-gated work_done with no human "go" (lead-ew86, ADR-050 D3 / ADR-018 D1-D2)

  The ADR-050 --orchestrator split regressed the tmux DEFAULT engage: the
  launcher's injected default startup prompt was reduced to
  arm-watcher-then-drain-then-"await user direction", so a tmux-default BC
  with pending dispatched inbox work would merely LIST those dispatches and
  then PARK, holding for a human "go" that never arrives in headless
  operation. This restores the autonomous default: the default startup
  prompt the launcher injects on the tmux-default path DIRECTS the agent to
  drain AND PROCESS each pending dispatch through the normal
  Implementer->Reviewer loop to a Reviewer-gated work_done, with no
  human-injected "go" required between the drain and the work_done. An
  explicit --startup-prompt remains a TOTAL override (unchanged).

  FIDELITY (test-fidelity-for-image-layer-container-runtime-scenarios): the
  step defs drive the REAL launcher (controller.launch over the
  FakeDockerDriver, resolving the default startup prompt exactly as the CLI
  does when no --startup-prompt is supplied) and bind to its ACTUAL recorded
  tmux send-keys — the injected default startup prompt — asserting the
  restored autonomous drain-AND-process directive is present and the "await
  user direction" park directive is absent. The launcher owns exactly the
  altitude of the default prompt it injects on the tmux-default path; the
  agent-runtime Implementer->Reviewer loop the prompt DIRECTS is out of this
  Python codebase's reach and is therefore asserted at the directive the
  launcher actually issues, never at a model.

  @scenario_hash:e811193fc061e1e8 @bc:shopsystem-bc-launcher
  Scenario: a tmux-default engaged BC with pending inbox dispatches processes them autonomously through the Implementer->Reviewer loop to a gated work_done, with no human "go"
    Given the container "bc-shopsystem-messaging" is launched on the DEFAULT "--orchestrator tmux" engage with no explicit interactive-startup override supplied
    And "shop-msg pending inbox --bc shopsystem-messaging" already lists one or more dispatched work_ids (for example an "assign_scenarios" or a "request_maintenance") that arrived before the engage started
    When the tmux-default engage arms its watcher and drains the pending inbox
    Then the engage does NOT merely LIST the pending dispatches and then hold awaiting a human "go", but proceeds to PROCESS each pending dispatch through the normal Implementer->Reviewer loop
    And each processed dispatch reaches a Reviewer-gated "work_done" emitted on its scenario path, with NO human-injected "go" keystroke required between the drain and the work_done
    And after the engage settles every pending dispatched work_id has a corresponding "work_done" in the BC outbox and NONE of those dispatches remains stuck pending in the BC inbox

  @scenario_hash:f65d43b1d8704f28 @bc:shopsystem-bc-launcher
  Scenario: the tmux-default autonomous engage processes only DISPATCHED inbox work and synthesizes no unrequested follow-on work
    Given the container "bc-shopsystem-messaging" is launched on the DEFAULT "--orchestrator tmux" engage
    And the BC inbox lists exactly the dispatched work_ids present at engage and no others
    When the tmux-default engage drains and processes its pending inbox work autonomously to work_done
    Then every "work_done" the engage emits corresponds to a work_id that was dispatched into the inbox, so the autonomy is bounded to dispatched work
    And the engage emits NO "work_done" for any work_id that was not dispatched into the inbox, synthesizing no unrequested follow-on work beyond what was dispatched
