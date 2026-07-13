@bc:shopsystem-bc-launcher @origin:lead-01jw.1
Feature: the watcher engage's finite runs (message-driven AND startup-drain) target the ONE shared per-container fabro server via FABRO_SERVER, so each runs to a Reviewer-gated work_done with NO second server-start (lead-oqaw / lead-01jw)

  FUNCTIONAL-SUCCESS sharpening of the structurally-pinned one-per-container
  watcher engage (728871aca27b0d8f + dc9a29a746921a14). The v0.3.67 engage
  started EXACTLY ONE shared per-container fabro server but each inbound-message
  finite `fabro run workflow.fabro` child then tried to START ITS OWN server;
  fabro refused with "Server already running (pid <n>)", the children exited 1
  with NO work_done, and their dispatches stuck pending in the BC inbox. The one
  fix — the finite child attaches to the ONE shared server via the already-
  exported `$FABRO_SERVER` (`fabro run --server "$FABRO_SERVER" ...`) instead of
  starting its own — routes through the SINGLE `run_finite` worker, so it fixes
  BOTH the message-driven watcher path and the startup-drain path at once. The
  count of resident fabro servers stays EXACTLY 1 and every dispatched work_id
  reaches a terminal work_done rather than sticking pending.

  FIDELITY: the step defs drive the REAL launcher (controller.launch over the
  FakeDockerDriver) and bind to its ACTUAL recorded `--orchestrator fabro`
  engage script — the ONE `fabro server start`, the shared-server `$FABRO_SERVER`
  the finite `fabro run` child targets, and the drain->dispatch->run_finite
  routing — never a model. The observable "no second server-start" property is
  exercised by executing the recorded finite-run invocation against a fabro stub
  that faithfully models fabro's "Server already running" refusal.

  @scenario_hash:9f785e78ed55da4b @bc:shopsystem-bc-launcher @origin:lead-01jw.1
  Scenario: with the single shared per-container fabro server already running, each of N>=2 message-driven finite runs executes to a Reviewer-gated work_done against that shared server with NO second server-start
    Given the container "bc-shopsystem-messaging" is running the "--orchestrator fabro" watcher engage with EXACTLY ONE long-lived shared per-container fabro server already started
    And two or more inbound messages, each carrying a distinct work_id on a scenario path, are delivered to the BC inbox so the watcher fires one finite "fabro run workflow.fabro" child per message
    When the finite children run
    Then EACH finite child runs SUCCESSFULLY against the already-running shared server rather than attempting to start its own server, so NO child fails with "Server already running (pid <n>)" and the count of resident fabro servers stays EXACTLY 1 throughout
    And EACH finite child reaches its terminal by driving the workflow to a Reviewer-gated "work_done" emitted on that message's scenario path, rather than exiting 1 with no work_done
    And after the finite runs complete every dispatched work_id has a corresponding "work_done" in the BC outbox and NONE of those dispatches remains stuck pending in the BC inbox

  @scenario_hash:32009f85a099be62 @bc:shopsystem-bc-launcher @origin:lead-01jw.1
  Scenario: the startup inbox drain fires one finite run per pre-existing pending work_id and each runs to a Reviewer-gated work_done against the single shared server
    Given the container "bc-shopsystem-messaging" starts the "--orchestrator fabro" watcher engage with its single long-lived shared per-container fabro server
    And "shop-msg pending inbox --bc shopsystem-messaging" already lists two or more work_ids that arrived before the watcher started, so the startup drain fires one finite "fabro run workflow.fabro" child per pending work_id
    When the startup drain runs its finite children
    Then EACH drained finite child runs SUCCESSFULLY against the single shared server with the resident fabro-server count staying EXACTLY 1 and NO child failing with "Server already running (pid <n>)"
    And EACH drained finite child reaches a Reviewer-gated "work_done" on its scenario path, so every pre-existing pending work_id is processed to terminal rather than left stuck pending
    And once the drain completes the pending-inbox set for those drained work_ids is empty because each produced a terminal "work_done"
