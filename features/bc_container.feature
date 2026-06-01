Feature: bc-container commands

  @scenario_hash:ad7ad2125c10df48 @bc:shopsystem-bc-launcher
  Scenario: bc-container launch starts a Docker container for the named BC
    Given the shopsystem-bc-launcher BC is installed
    And no Docker container named "bc-shopsystem-messaging" is running
    When I run bc-container launch with BC name "shopsystem-messaging" and a valid repo URL
    Then the command exits zero
    And a Docker container named "bc-shopsystem-messaging" is running

  @scenario_hash:97b5ca642b6eb8c9 @bc:shopsystem-bc-launcher
  Scenario: bc-container launch clones the BC repository inside the container
    Given the shopsystem-bc-launcher BC is installed
    And a BC named "shopsystem-messaging" with a valid repo URL is configured
    When I run bc-container launch with BC name "shopsystem-messaging"
    And the container starts
    Then the repository is cloned into the container's workspace directory
    And the cloned directory contains a git repository for "shopsystem-messaging"

  @scenario_hash:dee72338aaa9b96c @bc:shopsystem-bc-launcher
  Scenario: bc-container launch pulls beads state from the Dolt remote inside the container
    Given the shopsystem-bc-launcher BC is installed
    And a BC named "shopsystem-messaging" with a valid repo URL is configured
    When I run bc-container launch with BC name "shopsystem-messaging"
    And the container has cloned the repository
    Then bd dolt pull has been run inside the container's workspace directory
    And a .beads directory exists inside the container at the workspace root

  @scenario_hash:c1edb80e6ab9c55a @bc:shopsystem-bc-launcher
  Scenario: bc-container launch starts a named tmux session inside the container
    Given the shopsystem-bc-launcher BC is installed
    And no Docker container named "bc-shopsystem-messaging" is running
    When I run bc-container launch with BC name "shopsystem-messaging"
    And the container starts
    Then a tmux session named "agent" exists inside the container "bc-shopsystem-messaging"

  @scenario_hash:34d5fce28b2a2fe2 @bc:shopsystem-bc-launcher
  Scenario: bc-container launch reports state instead of starting a second container when the BC is already running
    Given the shopsystem-bc-launcher BC is installed
    And a Docker container named "bc-shopsystem-messaging" is already running
    When I run bc-container launch with BC name "shopsystem-messaging"
    Then the command exits zero
    And stdout reports that "bc-shopsystem-messaging" is already running
    And exactly one Docker container named "bc-shopsystem-messaging" is running

  @scenario_hash:81fc99e9245f7e13 @bc:shopsystem-bc-launcher
  Scenario: bc-container attach connects to the running BC container's tmux session
    Given the shopsystem-bc-launcher BC is installed
    And a Docker container named "bc-shopsystem-messaging" is running
    And a tmux session named "agent" exists inside the container
    When I run bc-container attach with BC name "shopsystem-messaging"
    Then the command executes docker exec -it bc-shopsystem-messaging tmux attach-session -t agent

  @scenario_hash:940a6e2c4180454b @bc:shopsystem-bc-launcher
  Scenario: bc-container monitor streams the BC container's tmux session output to host stdout
    Given the shopsystem-bc-launcher BC is installed
    And a Docker container named "bc-shopsystem-messaging" is running
    And a tmux session named "agent" exists inside the container containing the text "beads primed"
    When I run bc-container monitor with BC name "shopsystem-messaging"
    Then the command exits zero
    And stdout includes the text "beads primed"

  @scenario_hash:333d7ce8decefb5f @bc:shopsystem-bc-launcher
  Scenario: bc-container stop stops the named BC container
    Given the shopsystem-bc-launcher BC is installed
    And a Docker container named "bc-shopsystem-messaging" is running
    When I run bc-container stop with BC name "shopsystem-messaging"
    Then the command exits zero
    And no Docker container named "bc-shopsystem-messaging" is running

  @scenario_hash:7523a25a07e7a1cc @bc:shopsystem-bc-launcher
  Scenario: bc-container status reports running state for a running BC container
    Given the shopsystem-bc-launcher BC is installed
    And a Docker container named "bc-shopsystem-messaging" is running
    And a tmux session named "agent" exists inside the container
    When I run bc-container status with BC name "shopsystem-messaging"
    Then the command exits zero
    And stdout includes the BC name "shopsystem-messaging"
    And stdout includes the container state "running"
    And stdout includes the tmux session state "active"

  @scenario_hash:1205ae9d858332b6 @bc:shopsystem-bc-launcher
  Scenario: bc-container status reports stopped state for a stopped BC container
    Given the shopsystem-bc-launcher BC is installed
    And no Docker container named "bc-shopsystem-messaging" is running
    When I run bc-container status with BC name "shopsystem-messaging"
    Then the command exits zero
    And stdout includes the BC name "shopsystem-messaging"
    And stdout includes the container state "stopped"

  @scenario_hash:3f2f0d07156d8a2d @bc:shopsystem-bc-launcher
  Scenario: bc-container list shows all known BC containers with their states
    Given the shopsystem-bc-launcher BC is installed
    And a Docker container named "bc-shopsystem-messaging" is running
    And a Docker container named "bc-shopsystem-scenarios" is stopped
    When I run bc-container list
    Then the command exits zero
    And stdout includes an entry for "shopsystem-messaging" with state "running"
    And stdout includes an entry for "shopsystem-scenarios" with state "stopped"

  @scenario_hash:f39349c86d8d199c @bc:shopsystem-bc-launcher
  Scenario: bc-container launch propagates SHOPMSG_DSN to the container via the docker run -e flag
    Given the shopsystem-bc-launcher BC is installed
    And SHOPMSG_DSN is set to "postgresql://postgres:postgres@localhost:5432/shopsystem"
    And the FakeDockerDriver is active
    When I run bc-container launch with BC name "shopsystem-messaging"
    Then the FakeDockerDriver records that the docker run command for "bc-shopsystem-messaging" includes the flag "-e SHOPMSG_DSN=postgresql://postgres:postgres@localhost:5432/shopsystem"
    And the command exits zero

  @scenario_hash:4551023675b20b5c @bc:shopsystem-bc-launcher
  Scenario: bc-container launch forwards the exact SHOPMSG_DSN value from the host environment to the container
    Given the shopsystem-bc-launcher BC is installed
    And SHOPMSG_DSN is set to "postgresql://customhost:5432/mydb"
    And the FakeDockerDriver is active
    When I run bc-container launch with BC name "shopsystem-messaging"
    Then the FakeDockerDriver records that the docker run command for "bc-shopsystem-messaging" includes the flag "-e SHOPMSG_DSN=postgresql://customhost:5432/mydb"
    And the command exits zero

  @scenario_hash:791a18a1781a343e @bc:shopsystem-bc-launcher
  Scenario: bc-container is available on PATH after installing the shopsystem-bc-launcher package
    Given the shopsystem-bc-launcher BC package is installed in a Python environment
    When bc-container --help is executed in that environment
    Then the command exits zero
    And stdout includes the top-level subcommand names launch, attach, inject, monitor, stop, status, and list

  @scenario_hash:bb070f75d28648ae @bc:shopsystem-bc-launcher
  Scenario: shop-msg sent from the host is receivable by the BC agent inside the container
    Given the shopsystem-bc-launcher BC is installed
    And a Docker container named "bc-shopsystem-messaging" is running on the shared Docker network
    And the container has SHOPMSG_DSN set to the shared PostgreSQL instance
    When I run shop-msg send assign_scenarios on the host with work-id "lead-500" targeting the "shopsystem-messaging" BC
    Then the command exits zero
    And running shop-msg pending inside the container reports work-id "lead-500" as pending

  @scenario_hash:8ad37d1af8751f8c @bc:shopsystem-bc-launcher
  Scenario: shop-msg response written inside the BC container is readable from the host
    Given the shopsystem-bc-launcher BC is installed
    And a Docker container named "bc-shopsystem-messaging" is running on the shared Docker network
    And the container has SHOPMSG_DSN set to the shared PostgreSQL instance
    And an inbox message with work-id "lead-500" exists in the shared PostgreSQL backend
    When shop-msg respond work_done is run inside the container with work-id "lead-500"
    Then running shop-msg read outbox on the host with work-id "lead-500" exits zero
    And stdout includes message_type "work_done"

  @scenario_hash:d51650643f09e4f1 @bc:shopsystem-bc-launcher
  Scenario: The BC container does not have access to sibling BC source trees or the lead shop workspace
    Given the shopsystem-bc-launcher BC is installed
    And a temporary directory is created on the host as a candidate sibling mount
    And bc-container launch is run with BC name "shopsystem-messaging"
    And the container "bc-shopsystem-messaging" is running
    When the container's filesystem mounts are inspected
    Then the only bind mounts inside the container are the BC's own repository mount
    And no bind mount inside the container has the candidate directory as its source
