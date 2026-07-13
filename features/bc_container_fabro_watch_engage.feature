@bc:shopsystem-bc-launcher @origin:adr-058
Feature: the --orchestrator fabro engage is an EXTERNAL agent-free message-driven watcher supervisor backed by ONE long-lived per-container fabro server (lead-1vbw / lead-01jw)

  Durable launcher-side fix for the lead-01jw fabro-server memory leak: the
  prior `--orchestrator fabro` engage ran ONE never-ending `fabro run
  dispatcher.toml` (a cyclic poll->dispatch->wait->poll loop whose per-tick
  run-graph events accumulated UNBOUNDED in the fabro server heap — RSS grew
  18->28GiB during PURE idle polling, OOM-bound) and maintained NO shop-msg
  heartbeat (lead-8hpz: live-but-offline). The engage becomes an EXTERNAL,
  agent-free, message-driven watcher supervisor whose ONLY always-resident
  process is `shop-msg watch --bc <name>` (LISTEN/NOTIFY + bc_presence
  heartbeat). Each inbound message fires ONE FINITE `fabro run workflow.fabro`
  child (the UNCHANGED ADR-051 child def, provider=local, WORK_ID/BC_NAME via
  [run.environment.env]) against EXACTLY ONE long-lived per-container fabro
  server (product-authority directive: NOT one ephemeral server per run), which
  exposes a scrapeable telemetry surface. Idle => zero resident runs.

  FIDELITY (test-fidelity-for-image-layer-container-runtime-scenarios): the
  step defs drive the REAL launcher (controller.launch over the
  FakeDockerDriver) and bind to its ACTUAL recorded `--orchestrator fabro`
  engage script — the watcher supervisor, the one per-container server, the
  telemetry surface, the drain/dedup/non-fatal machinery — never to a model.
  Dockerless: validated structurally over the recorded engage script, never by
  fabricating a live-server / 50-run observation.

  @scenario_hash:47da82f60bbd47a9 @bc:shopsystem-bc-launcher
    Scenario: the --orchestrator fabro engage starts the external agent-free message-driven watcher and runs NO long-lived "fabro run dispatcher.toml", so no infinite run accumulates fabro server heap
    Given the shopsystem-bc-launcher BC is installed
    And bc-container launch is run for BC name "shopsystem-messaging" on the fabro orchestrator launch path selected by "--orchestrator fabro"
    And the container "bc-shopsystem-messaging" is running with the self-contained fabro def set POURED by shop-templates into "/workspace/.fabro/", including the UNCHANGED ADR-051 "workflow.fabro" finite child def
    When the launcher's recorded "--orchestrator fabro" engage command is inspected structurally, without a live docker daemon, a running fabro server, or a reachable agent-vault
    Then the engage starts the external message-driven watcher supervisor whose ONLY always-resident process is "shop-msg watch --bc shopsystem-messaging" (which holds NO run-graph and emits a line only on a real inbox message, never per poll tick), and which fires exactly ONE finite "fabro run workflow.fabro" child per inbound inbox message
    And the engage does NOT run a long-lived "fabro run dispatcher.toml" nor "fabro run dispatcher.fabro": there is NO infinite cyclic poll->dispatch->wait->poll run resident in a fabro server, so the per-tick run-graph event accumulation that grew the server heap 18->28GiB during pure idle polling (lead-01jw) cannot occur
    And with no inbound message in flight the engage holds ZERO resident fabro runs, so steady-state idle retains no per-run event state at all

  @scenario_hash:728871aca27b0d8f @bc:shopsystem-bc-launcher
    Scenario: the fabro engage starts exactly ONE long-lived fabro server for the container lifetime and fires each finite child against that single shared server, NOT one ephemeral server per run
    Given the shopsystem-bc-launcher BC is installed
    And bc-container launch is run for BC name "shopsystem-messaging" on the fabro orchestrator launch path selected by "--orchestrator fabro"
    And the container "bc-shopsystem-messaging" is running the external message-driven watcher engage (scenario 1)
    When the watcher's fabro-server lifecycle and its per-message finite-run invocation are inspected structurally, without a live docker daemon, a running fabro server, or a reachable agent-vault
    Then the engage starts EXACTLY ONE fabro server once, bound to a single container-scoped socket, and that one server persists for the whole container lifetime rather than being started and killed per run
    And each inbound message fires a finite "fabro run workflow.fabro" child against that ONE shared server (the child's FABRO_SERVER targets the shared container socket), so the count of resident fabro servers is exactly 1 whether 0, 1, or many finite runs are in flight
    And as the negative control, the engage does NOT start one ephemeral fabro server per run and kill it on completion (the prior reference-workaround shape in bin/bc-fabro-watch), because a per-run server vanishes before it can be scraped whereas the single persistent server is observable (scenario 3)

  @scenario_hash:edc035fdde4062df @bc:shopsystem-bc-launcher
    Scenario: the single per-container fabro server exposes telemetry sufficient to observe its memory and finite-run activity over time
    Given the shopsystem-bc-launcher BC is installed
    And the container "bc-shopsystem-messaging" is running the external watcher engage with exactly one long-lived per-container fabro server (scenario 2)
    When the per-container server's observability surface is inspected while the container runs
    Then the single per-container fabro server exposes a telemetry/metrics surface that can be scraped for at minimum the server's current resident memory and its active and completed finite-run counts over time
    And because the server is long-lived and singular this telemetry is CONTINUOUSLY observable across the container lifetime, which is the deciding reason the engage uses ONE per-container server rather than an ephemeral-per-run server that vanishes before it can be measured
    And the telemetry is sufficient to detect whether the server's retained memory returns toward baseline or grows monotonically across successive finite runs, serving as the measurement instrument scenario 4 asserts against

  @scenario_hash:4d2411e2050345bc @bc:shopsystem-bc-launcher
    Scenario: across many sequential message-driven finite runs the shared per-container server's retained memory returns toward baseline and stays bounded, not monotonically climbing
    Given the shopsystem-bc-launcher BC is installed
    And the container "bc-shopsystem-messaging" is running the external watcher engage with exactly one long-lived per-container fabro server (scenario 2) exposing run and memory telemetry (scenario 3)
    And a baseline of the per-container server's resident memory is recorded while no finite run is in flight
    When at least 50 sequential inbound messages each fire and drive a finite "fabro run workflow.fabro" child to its terminal (done or halt) against the shared server, one after another
    Then after each finite run reaches its terminal the shared server RELEASES that run's event state, so the server's retained resident memory returns toward the recorded baseline rather than retaining the completed run's events
    And after all the runs the server's peak retained resident memory is within a bounded delta of baseline and does NOT increase monotonically with the run count, so the shared-server memory is BOUNDED across many runs even though it no longer comes "for free" from per-run process death
    And if instead the telemetry shows retained memory climbing monotonically with the run count, that is the fabro-side reclamation defect escalated in lead-01jw which the telemetry (scenario 3) makes visible as the escalation signal, and the required behavior this scenario pins remains the bounded one

  @scenario_hash:e94a01b26ed6a4cc @bc:shopsystem-bc-launcher
    Scenario: the watcher maintains the bc_presence heartbeat via "shop-msg watch" so bc-status reports the fabro-engaged BC online
    Given the shopsystem-bc-launcher BC is installed
    And the container "bc-shopsystem-messaging" is running the external watcher engage (scenario 1)
    When the always-resident "shop-msg watch --bc shopsystem-messaging" process runs for longer than the bc-status staleness window
    Then that same process UPSERTs the bc_presence (bc_name, last_seen_at) heartbeat on a cadence inside the staleness window, so "shop-msg bc-status" classifies "shopsystem-messaging" as ONLINE and the container healthcheck reports healthy
    And as the negative control, the superseded infinite "fabro run dispatcher.toml" engage maintained NO shop-msg heartbeat, so a fabro BC was live-and-working yet reported offline with a stale heartbeat and an unhealthy healthcheck (lead-8hpz), which this watcher-maintained heartbeat fixes

  @scenario_hash:7a4f7eed52594107 @bc:shopsystem-bc-launcher
    Scenario: the dispatch path is agent-free and a failed finite child is non-fatal, so the watcher keeps serving
    Given the shopsystem-bc-launcher BC is installed
    And the container "bc-shopsystem-messaging" is running the external watcher engage (scenario 1)
    When the watcher's dispatch path is inspected structurally and a finite child run for one message terminates with a NON-zero exit
    Then NO claude, LLM, or model-backed agent appears anywhere in the watcher's dispatch path: the always-resident process is "shop-msg watch" and each dispatch fires a finite native "fabro run workflow.fabro" child, so steady-state supervision spends ZERO model tokens
    And a finite child that exits non-zero is logged and swallowed as NON-FATAL: it does not terminate the watcher, the always-resident "shop-msg watch" keeps running, that child's in-flight lock is released, and subsequent inbound messages continue to be dispatched

  @scenario_hash:9d737bcd0f4473e9 @bc:shopsystem-bc-launcher
    Scenario: work intake is idempotent — startup drains the pre-existing pending inbox and in-flight dedup prevents running one work_id twice concurrently
    Given the shopsystem-bc-launcher BC is installed
    And the container "bc-shopsystem-messaging" is running the external watcher engage (scenario 1)
    And the inbox already holds pending messages that arrived before the watcher started, and "shop-msg pending inbox --bc shopsystem-messaging" is the authoritative pending set
    When the watcher starts and, while a finite child for work id "W" is still in flight, another wake for the same "W" arrives
    Then on startup the watcher DRAINS the pre-existing pending inbox by firing a finite child for each pending work id, so no message that arrived between sessions is missed
    And while a child for "W" is in flight a second wake for "W" is SKIPPED by in-flight dedup, so exactly one child runs per work_id concurrently and duplicate children cannot collide on the shared per-"W" worktree
    And once "W"'s child reaches its terminal its in-flight lock is released, so a genuinely new later message reusing "W" dispatches again
