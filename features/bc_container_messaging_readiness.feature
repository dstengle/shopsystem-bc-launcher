Feature: bc-container launch gates the startup prompt behind a messaging readiness barrier

  @scenario_hash:e6543853e4333506 @bc:shopsystem-bc-launcher
  Scenario: bc-container launch surfaces a readiness failure when the messaging database is unreachable, before the agent engages
    Given the shopsystem-bc-launcher BC is installed
    And no Docker container named "bc-shopsystem-messaging" is running
    And SHOPMSG_DSN for the container points at an address where no reachable database is listening
    When I run bc-container launch with BC name "shopsystem-messaging" and a startup prompt
    Then the command exits non-zero
    And stderr reports a messaging readiness failure that names the SHOPMSG_DSN value
    And no startup prompt has been sent to the tmux session named "agent" in container "bc-shopsystem-messaging"

  @scenario_hash:11778d987b2fc50f @bc:shopsystem-bc-launcher
  Scenario: bc-container launch does not inject the startup prompt until the readiness barrier passes
    Given the shopsystem-bc-launcher BC is installed
    And no Docker container named "bc-shopsystem-messaging" is running
    When I run bc-container launch with BC name "shopsystem-messaging" and a startup prompt
    And the container is up but the readiness sequence has not yet completed
    Then no startup prompt has been sent to the tmux session named "agent" in container "bc-shopsystem-messaging"
    And once the readiness sequence completes successfully, the startup prompt is sent to the tmux session named "agent"

  @scenario_hash:11778d987b2fc50f @bc:shopsystem-bc-launcher
  Scenario: re-running the readiness sequence against an already-ready container is a no-op that reports ready
    Given the shopsystem-bc-launcher BC is installed
    And a Docker container named "bc-shopsystem-messaging" is running and has already passed its readiness sequence
    When I run the readiness sequence against container "bc-shopsystem-messaging" a second time
    Then the command exits zero
    And it reports that "bc-shopsystem-messaging" is already ready
    And no startup prompt has been re-sent to the tmux session named "agent" in container "bc-shopsystem-messaging"
