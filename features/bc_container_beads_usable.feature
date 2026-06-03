Feature: bc-container launch leaves beads functionally usable inside the container

  @scenario_hash:1f1d178bca957fbc @bc:shopsystem-bc-launcher
  Scenario: after bc-container launch, beads is functionally usable inside the container with a configured issue prefix
    Given the shopsystem-bc-launcher BC is installed
    And a BC named "shopsystem-messaging" with a valid repo URL is configured
    When I run bc-container launch with BC name "shopsystem-messaging"
    And the container has cloned the repository and bd dolt pull has been run inside the workspace directory
    Then the beads issue_prefix configured inside the container's .beads is non-empty and matches the BC's expected prefix
    And bd create run inside the container's workspace directory exits zero and yields a new issue id carrying that prefix
    And bd ready run inside the container's workspace directory exits zero
