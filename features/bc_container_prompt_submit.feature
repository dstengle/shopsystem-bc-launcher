Feature: bc-container --startup-prompt and inject commit prompts to the agent (lead-xsmn / lead-hyee)

  # Scenarios 27 (0e733774844ed9f3) and 28 (17518db1dc1c9001) were RETIRED
  # under ADR-010 by lead-lez1: they pinned the single-invocation
  # `send-keys <text> Enter` shape, which lead-9q0f empirically refuted as the
  # paste-absorption root cause.  Their successors are scenarios 30
  # (6477b2ab3720ac53) and 31 (ad68aaf60377706e) in
  # features/bc_container_prompt_submit_two_call.feature, which pin the
  # two-discrete-invocation shape that actually commits.

  @scenario_hash:5ef728039884a9a2 @bc:shopsystem-bc-launcher
  Scenario: bc-container monitor surfaces an agent-working state-marker within a bounded interval of bc-container launch --startup-prompt exiting, with no human or host-side follow-up keystroke
    Given the shopsystem-bc-launcher BC is installed
    And no Docker container named "bc-shopsystem-messaging" is running
    When I run "bc-container launch shopsystem-messaging --startup-prompt 'bd prime'" and the launch command exits zero
    And I run "bc-container monitor shopsystem-messaging" and read its streamed output without issuing any further "bc-container inject" or other host-side keystroke
    Then within 30 seconds of the launch command exiting, the streamed monitor output contains an agent-working state-marker line that is produced only when the agent has committed input and is actively processing it (and not produced when the agent is idle at an unsubmitted input buffer)
    And the agent-working state-marker appears as a direct consequence of the launch's --startup-prompt being submitted, with no intervening "bc-container inject" invocation
