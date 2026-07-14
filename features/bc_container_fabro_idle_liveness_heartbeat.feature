@bc:shopsystem-bc-launcher @origin:adr-050
Feature: the --orchestrator fabro engage supervisor maintains a MESSAGE-INDEPENDENT bc_presence heartbeat so an idle-but-live BC reports online and healthy (lead-8hpz)

  Bugfix lead-8hpz (ADDITIVE; extends the structural liveness pin
  e94a01b26ed6a4cc, ADR-050 D3). The `--orchestrator fabro` engage replaced the
  tmux session-start loop with an EXTERNAL agent-free watcher supervisor whose
  ONLY always-resident process is `shop-msg watch --bc <name>` — a LISTEN/NOTIFY
  event source that emits a line ONLY on a real message and NEVER per poll tick.
  A watcher that wakes only on messages advances NO bc_presence heartbeat while
  idle, so an idle-but-live BC's last_seen_at ages past the bc-status staleness
  window (operator-confirmed ~2525s) and the BC reports OFFLINE + the container
  healthcheck reports UNHEALTHY even though it is functionally healthy. THE FIX:
  the always-resident supervisor UPSERTs the bc_presence heartbeat on a bounded
  cadence MESSAGE-INDEPENDENTLY (NOT per-poll-tick, NOT only-when-work-in-flight;
  the superseded "emit a heartbeat each 5s poll" fix-direction is SUPERSEDED),
  mirroring the telemetry sampler cadence and bounded strictly below the
  bc-status staleness window, so an idle-but-live BC stays ONLINE and healthy.

  FIDELITY: the step defs drive the REAL launcher (controller.launch over the
  FakeDockerDriver, launch_path="fabro") and bind to its ACTUAL recorded
  `--orchestrator fabro` engage `/bin/sh -c` script — never a model, never a
  shallow string-match. TEETH: remove the message-independent heartbeat cadence
  from `_fabro_engage_script` and this scenario REDs.

  @scenario_hash:a5ce1af45ade7444 @bc:shopsystem-bc-launcher
  Scenario: an idle-but-live fabro-engaged BC — supervisor resident, no message in flight — reports bc-status online and healthcheck healthy, not offline and not unhealthy
    Given the container "bc-shopsystem-messaging" is running the "--orchestrator fabro" watcher engage with its always-resident supervisor process running
    And NO inbound message is in flight, so the BC is idle-but-live with zero resident finite runs
    When the BC runs idle for longer than the bc-status staleness window with no dispatched work arriving
    Then "shop-msg bc-status" classifies "shopsystem-messaging" as ONLINE because its last_seen_at heartbeat is within the staleness window, NOT offline with a stale heartbeat
    And the container healthcheck reports healthy, NOT unhealthy, for the idle-but-live BC
    And this closes the lead-8hpz regression where a functionally healthy fabro BC reported offline and unhealthy because the fabro engage maintained no shop-msg heartbeat after replacing the tmux session-start loop

  # Behavior 2 (@scenario_hash:90e6b9fae7a63eb8, ADDITIVE — extends
  # a5ce1af45ade7444). Behavior 1 pinned the message-independent cadence's RAW
  # sleep interval below the window; this pins the EFFECTIVE heartbeat period (the
  # cadence `sleep` interval PLUS the per-tick bounded `shop-msg watch` timeout —
  # the worst-case age between successive UPSERTs) BOUNDED strictly below the REAL
  # bc-status ONLINE staleness window (shop_msg PRESENCE_ONLINE_MAX_SECONDS = 90).
  # TEETH: the launcher REFUSES to build the engage (raises) if the effective
  # period reaches/exceeds the staleness window. NEGATIVE CONTROL: the superseded
  # infinite `fabro run dispatcher.toml` engage maintained NO shop-msg heartbeat on
  # ANY cadence (last_seen_at aged unboundedly -> a live BC reported offline, the
  # lead-8hpz P0), which this bounded cadence replaces.
  @scenario_hash:90e6b9fae7a63eb8 @bc:shopsystem-bc-launcher
  Scenario: the fabro heartbeat cadence is bounded strictly inside the bc-status staleness window, so a live BC's last_seen_at never goes stale
    Given the container "bc-shopsystem-messaging" is running the "--orchestrator fabro" watcher engage with its always-resident supervisor maintaining the shop-msg heartbeat
    When the supervisor runs continuously for several multiples of the bc-status staleness window while the BC stays live
    Then the supervisor UPSERTs the bc_presence (bc_name, last_seen_at) heartbeat on a cadence whose interval is BOUNDED strictly below the bc-status staleness window, independent of whether any message arrives
    And because the cadence interval is below the staleness window, the last_seen_at never ages past the staleness threshold while the supervisor is alive, so a live BC never flaps to offline between heartbeats
    And as the negative control, the superseded infinite "fabro run dispatcher.toml" engage maintained NO shop-msg heartbeat on any cadence, so its last_seen_at aged unboundedly and a live BC reported offline (lead-8hpz), which this bounded cadence fixes
