@bc:shopsystem-bc-launcher @origin:lead-01jw.2
Feature: the watcher engage binds the ONE shared per-container fabro server to EXACTLY the address it exports as FABRO_SERVER, so each finite "fabro run --server $FABRO_SERVER" child CONNECTS to the real running shared server — proven against a REAL fabro server, not a stub (lead-01jw.2, iteration-3)

  ITERATION-3 DURABLE FIX for the recurring P0 "Server already running" defect.
  The v0.3.67 (lead-1vbw) and v0.3.68 (lead-oqaw) fixes shipped BROKEN because
  their tests were FakeDockerDriver structural assertions over the recorded
  engage script text — address-blind. A stubbed server binds no socket at the
  agreed address, so those tests could never expose the real defect.

  EMPIRICAL ROOT CAUSE (v0.3.68, real container + verified here with the real
  fabro 0.254.0 binary): the engage's `fabro install` daemonizes a server on
  fabro's DEFAULT TCP endpoint 127.0.0.1:32276 AND writes SESSION_SECRET +
  FABRO_DEV_TOKEN to the DEFAULT storage dir's `server.env`. The engage then
  runs `fabro server start --bind <socket> --storage-dir <custom .watch dir>`,
  which DIES "auth is configured but SESSION_SECRET is not set" because the
  custom storage dir has no server.env and the engage never exports the secret.
  So the intended one shared SOCKET server never comes up; the only resident
  server is the install daemon on TCP 32276, while FABRO_SERVER points at a
  unix socket NO server listens on. Each finite `fabro run --server <socket>`
  child cannot connect, falls back to `fabro server start` (default TCP 32276),
  collides with the install daemon, and fails "Server already running (pid <n>)
  on 127.0.0.1:32276", exits 1, no work_done, dispatch stuck pending.

  THE FIX: after `fabro install`, stop the install-daemonized default server
  (so the resident count returns to exactly 1) and export SESSION_SECRET +
  FABRO_DEV_TOKEN from the install-written server.env, so the ONE shared server
  actually starts BOUND to the socket — the SAME address exported as
  FABRO_SERVER and passed to every `fabro run --server`. Bind == target.

  FIDELITY: the acceptance test brings the ONE shared server up by executing the
  REAL launcher's recorded `--orchestrator fabro` engage bootstrap (derived from
  `_fabro_engage_script`, container paths redirected to a temp layout, real
  `fabro install`), then fires a real `fabro run --server "$FABRO_SERVER"`
  finite child against it. A stubbed / address-disagreeing server reproduces the
  v0.3.68 "Server already running" failure — so the proof is RED against any
  stub and GREEN only against the real running shared server.

  @scenario_hash:ab9b2be40558cfc2 @bc:shopsystem-bc-launcher
  Scenario: the shared server's bind address and the exported FABRO_SERVER client target AGREE, so a message-driven finite run CONNECTS to the running shared server with no second server-start
    Given the container "bc-shopsystem-messaging" is running the "--orchestrator fabro" watcher engage with EXACTLY ONE long-lived shared per-container fabro server already started and bound to a single container-scoped address
    And the address that one shared server is bound to is EXACTLY the address the engage exports as "FABRO_SERVER" and passes to each finite "fabro run --server", whether that address is a unix socket or a TCP endpoint
    And an inbound message carrying a work_id on a scenario path is delivered so the watcher fires one finite "fabro run workflow.fabro --server $FABRO_SERVER" child
    When the finite child runs against the real running shared server
    Then the finite child's connection to "$FABRO_SERVER" is ACCEPTED by the already-running shared server — a real client-to-server connection is established at the agreed address — so the child does NOT run "fabro server start", does NOT fall back to starting its own server, and NO child fails with "Server already running (pid <n>)"
    And the count of resident fabro servers stays EXACTLY 1 throughout, because the finite run connected to the existing server rather than binding a second one at a different address
    And the finite child advances on the real server to a Reviewer-gated "work_done" emitted on that message's scenario path via the real shop-msg / bc-emit path, so the dispatched work_id lands a real "work_done" in the BC outbox and is no longer stuck pending — an outcome unreachable if bind and target addresses disagreed

  @scenario_hash:33488b7e1657b7c7 @bc:shopsystem-bc-launcher
  Scenario: a startup-drain finite run CONNECTS to the same real shared server at the agreed address and reaches a Reviewer-gated work_done
    Given the container "bc-shopsystem-messaging" starts the "--orchestrator fabro" watcher engage with its single long-lived shared per-container fabro server bound to a container-scoped address that EQUALS the exported "FABRO_SERVER"
    And "shop-msg pending inbox --bc shopsystem-messaging" already lists a work_id that arrived before the watcher started, so the startup drain fires a finite "fabro run workflow.fabro --server $FABRO_SERVER" child for it
    When the startup-drain finite child runs against the real running shared server
    Then the drained child's connection to "$FABRO_SERVER" is ACCEPTED by the single running shared server at the agreed address, so it does NOT start its own server and does NOT fail with "Server already running (pid <n>)", and the resident fabro-server count stays EXACTLY 1
    And the drained child advances on the real server to a Reviewer-gated "work_done" on its scenario path via the real shop-msg / bc-emit path, so once the drain completes that pre-existing work_id is processed to terminal and no longer appears in the pending inbox

  @scenario_hash:89e975a7a38fdcaf @bc:shopsystem-bc-launcher
  Scenario: the finite-run success is DEMONSTRATED against a real running server such that a stubbed or faked server cannot satisfy it — the real server's telemetry records the run active->completed for the work_id
    Given the container "bc-shopsystem-messaging" is running the watcher engage with its single REAL long-lived shared per-container fabro server exposing scrapeable run telemetry (the @scenario_hash:edc035fdde4062df surface)
    And a baseline scrape of that telemetry shows ZERO active finite runs and the completed-run count for a new work_id is zero
    When an inbound message fires a finite "fabro run workflow.fabro --server $FABRO_SERVER" child that connects to the real running shared server and is driven to its terminal
    Then the REAL shared server's own telemetry records that finite run transitioning ACTIVE while it executes and then COMPLETED for that work_id — evidence that only a real server accepting a real finite-run connection can produce, and that a stubbed / faked docker-driver server (which binds no socket at the agreed address and exposes no such telemetry) CANNOT produce
    And a real "work_done" for that work_id lands in the BC outbox via the real shop-msg / bc-emit path, so the terminal is proven by the real messaging surface rather than by a canned driver return
    And as the negative control, were the server stubbed or its bind address to disagree with the exported "FABRO_SERVER", the finite run would find no server to connect to at the agreed address and would reproduce the v0.3.68 failure — "Server already running (pid <n>)" with the child "exited 1", no telemetry-observed completed run, and no real "work_done" — so this scenario is RED against any stubbed or address-disagreeing server and GREEN only against the real running shared server
