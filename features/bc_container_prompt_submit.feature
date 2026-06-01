Feature: bc-container --startup-prompt and inject commit prompts to the agent (lead-xsmn / lead-hyee)

  @scenario_hash:0e733774844ed9f3 @bc:shopsystem-bc-launcher
  Scenario: bc-container launch with --startup-prompt commits the prompt to the agent's input loop on its own, with no follow-up keystroke from the host required
    Given the shopsystem-bc-launcher BC is installed
    And no Docker container named "bc-shopsystem-messaging" is running
    When I run "bc-container launch shopsystem-messaging --startup-prompt 'bd prime'" and the launch command exits zero
    Then no subsequent "bc-container inject" invocation (whether with a non-empty prompt or an empty prompt acting as a forced Enter) is required for the in-container agent to begin processing the prompt "bd prime"
    And the in-container agent's input loop has been committed the prompt "bd prime" as a submitted input, not as an unsubmitted buffer entry awaiting an Enter keystroke
    And the agent's observable state transitions from idle to actively processing the prompt "bd prime" as a direct consequence of the launch command completing

  @scenario_hash:17518db1dc1c9001 @bc:shopsystem-bc-launcher
  Scenario: bc-container inject commits the prompt to the agent's input loop on its own, with no follow-up keystroke from the host required
    Given the shopsystem-bc-launcher BC is installed
    And a Docker container named "bc-shopsystem-messaging" is running with a tmux session named "agent" hosting an interactive agent at its input prompt
    When I run "bc-container inject shopsystem-messaging 'bd prime'" and the command exits zero
    Then no subsequent "bc-container inject" invocation (whether with a non-empty prompt or an empty prompt acting as a forced Enter) is required for the in-container agent to begin processing the prompt "bd prime"
    And the in-container agent's input loop has been committed the prompt "bd prime" as a submitted input, not as an unsubmitted buffer entry awaiting an Enter keystroke
    And the agent's observable state transitions from idle to actively processing the prompt "bd prime" as a direct consequence of the inject command completing

  @scenario_hash:5ef728039884a9a2 @bc:shopsystem-bc-launcher
  Scenario: bc-container monitor surfaces an agent-working state-marker within a bounded interval of bc-container launch --startup-prompt exiting, with no human or host-side follow-up keystroke
    Given the shopsystem-bc-launcher BC is installed
    And no Docker container named "bc-shopsystem-messaging" is running
    When I run "bc-container launch shopsystem-messaging --startup-prompt 'bd prime'" and the launch command exits zero
    And I run "bc-container monitor shopsystem-messaging" and read its streamed output without issuing any further "bc-container inject" or other host-side keystroke
    Then within 30 seconds of the launch command exiting, the streamed monitor output contains an agent-working state-marker line that is produced only when the agent has committed input and is actively processing it (and not produced when the agent is idle at an unsubmitted input buffer)
    And the agent-working state-marker appears as a direct consequence of the launch's --startup-prompt being submitted, with no intervening "bc-container inject" invocation
