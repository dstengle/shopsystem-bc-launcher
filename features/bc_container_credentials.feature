Feature: bc-container launch brokers BC-container credentials through agent-vault

  # ADR-026 (accepted 2026-06-09) supersedes the host-credential-mount model.
  # Zero host-filesystem credential coupling reaches a BC container, for BOTH
  # Claude OAuth and GitHub.  The agent-vault broker is the SOLE credential
  # path; there is no launch-mode flag and no host-mount fallback.  Dispatched
  # on lead-v4ih, unblocked by lead-hxb8.

  @scenario_hash:6952248a419ca56b @bc:shopsystem-bc-launcher
  Scenario: a launched BC container has no host ~/.claude credential directory mount
    Given the shopsystem-bc-launcher BC is installed
    And bc-container launch is run with BC name "shopsystem-messaging"
    And the container "bc-shopsystem-messaging" is running
    When the container's bind mounts are inspected via docker inspect
    Then no bind mount inside the container has the host "~/.claude" directory as its source
    And no bind mount inside the container targets "/home/vscode/.claude" as a read-write directory mount

  @scenario_hash:f838de07a80749f9 @bc:shopsystem-bc-launcher
  Scenario: a launched BC container has no host gh or gitconfig credential mount
    Given the shopsystem-bc-launcher BC is installed
    And bc-container launch is run with BC name "shopsystem-messaging"
    And the container "bc-shopsystem-messaging" is running
    When the container's bind mounts are inspected via docker inspect
    Then no bind mount inside the container has the host "~/.config/gh" directory as its source
    And no bind mount inside the container has the host "~/.gitconfig" file as its source

  @scenario_hash:d6296d959f851be5 @bc:shopsystem-bc-launcher
  Scenario: bc-container launch does not require BCLAUNCHER_HOST_HOME to resolve a credential mount source
    Given the shopsystem-bc-launcher BC is installed
    And the environment variable BCLAUNCHER_HOST_HOME is unset
    When I run bc-container launch with BC name "shopsystem-messaging"
    Then the command exits zero and the container "bc-shopsystem-messaging" is running
    And launch did not fail resolving any host credential path

  @scenario_hash:c4e88075a0b4bd00 @bc:shopsystem-bc-launcher
  Scenario: the launched Claude agent is invoked wrapped in agent-vault run
    Given the shopsystem-bc-launcher BC is installed
    And an agent-vault broker is running on the shopsystem network and is reachable
    When bc-container launch starts the agent for BC name "shopsystem-messaging"
    Then the command line that launches the agent inside the tmux session named "agent" invokes "agent-vault run -- claude"
    And the agent process environment sets HTTPS_PROXY to the agent-vault broker's proxy listener on the shopsystem network

  # REVISED under operator design directive (no controller bind mounts): the
  # placeholder .credentials.json is now BAKED INTO the bc-base image rather
  # than mounted read-only by the controller. The negative-security invariant
  # is PRESERVED (no real OAuth token anywhere in the container); only the
  # delivery mechanism changes mount -> baked.
  @scenario_hash:3931e43e01824a3c @bc:shopsystem-bc-launcher
  Scenario: the container's Claude credential file is a placeholder baked into the image, never the real OAuth credential
    Given the shopsystem-bc-launcher BC is installed
    And bc-container launch is run with BC name "shopsystem-messaging"
    And the container "bc-shopsystem-messaging" is running
    When the placeholder ".credentials.json" baked into the bc-base image is read
    Then its accessToken field has the literal value "__PLACEHOLDER__"
    And the placeholder credentials file is baked into the image at "/home/vscode/.claude/.credentials.json"
    And the controller builds no credential bind-mount into the container
    And the real host OAuth accessToken value does not appear anywhere in the container's filesystem

  @scenario_hash:97734ca69a510e37 @bc:shopsystem-bc-launcher
  Scenario: an authenticated GitHub operation from inside the container succeeds via the broker with no mounted GitHub credential
    Given the shopsystem-bc-launcher BC is installed
    And an agent-vault broker with a GitHub credential service is running on the shopsystem network and is reachable
    And the container "bc-shopsystem-messaging" is running with no host gh or gitconfig credential mounted
    When an authenticated GitHub operation is run from inside the container through the agent-vault broker
    Then the operation completes successfully against GitHub
    And no GitHub token value is present in the container's environment or filesystem

  @scenario_hash:f23dfbe84c899968 @bc:shopsystem-bc-launcher
  Scenario: the broker substitutes the GitHub credential on the outbound request rather than the container holding it
    Given an agent-vault broker with a GitHub credential service is running on the shopsystem network
    And the container "bc-shopsystem-messaging" routes its GitHub-bound traffic through the broker's proxy listener
    When a git operation inside the container makes an authenticated request to github.com
    Then the request the broker forwards to github.com carries the broker-stored GitHub credential
    And the request as it leaves the container carries no GitHub credential

  @scenario_hash:6cb07698a874aa47 @bc:shopsystem-bc-launcher
  Scenario: bc-container launch surfaces a readiness failure when the agent-vault broker is unreachable, before the agent engages
    Given the shopsystem-bc-launcher BC is installed
    And no Docker container named "bc-shopsystem-messaging" is running
    And the agent-vault broker address configured for the container points at an address where no reachable broker is listening
    When I run bc-container launch with BC name "shopsystem-messaging" and an agent-vault startup prompt
    Then the command exits non-zero
    And stderr reports an agent-vault readiness failure that names the configured agent-vault broker address
    And no startup prompt has been sent to the tmux session named "agent" in container "bc-shopsystem-messaging"

  @scenario_hash:3b2a81c1bfe2897e @bc:shopsystem-bc-launcher
  Scenario: a BC container whose agent-vault broker is unreachable reports unhealthy despite the process being alive
    Given a BC container named "bc-shopsystem-messaging" is running with its agent process alive
    And the agent-vault broker configured for the container is not reachable
    When I inspect the container's health status via docker inspect
    Then the container's reported health status is "unhealthy"

  @scenario_hash:f73afae009c283fc @bc:shopsystem-bc-launcher
  Scenario: the readiness barrier passes and engages the agent only when both the messaging database and the agent-vault broker are reachable
    Given the shopsystem-bc-launcher BC is installed
    And no Docker container named "bc-shopsystem-messaging" is running
    And the messaging database at SHOPMSG_DSN is reachable for the agent-vault launch
    And the agent-vault broker on the shopsystem network is reachable
    When I run bc-container launch with BC name "shopsystem-messaging" and a brokered startup prompt
    Then the readiness barrier reports both messaging-database and agent-vault checks passed
    And the startup prompt is sent to the tmux session named "agent" in container "bc-shopsystem-messaging"

  @scenario_hash:64aaff804dc4bf98 @bc:shopsystem-bc-launcher
  Scenario: the readiness barrier withholds engagement when the messaging database is reachable but the agent-vault broker is not
    Given the shopsystem-bc-launcher BC is installed
    And no Docker container named "bc-shopsystem-messaging" is running
    And the messaging database at SHOPMSG_DSN is reachable for the agent-vault launch
    And the agent-vault broker on the shopsystem network is not reachable
    When I run bc-container launch with BC name "shopsystem-messaging" and a brokered startup prompt
    Then the command exits non-zero
    And no startup prompt has been sent to the tmux session named "agent" in container "bc-shopsystem-messaging"

  @scenario_hash:2a4e9889c141c790 @bc:shopsystem-bc-launcher
  Scenario: brokered launch presupposes the broker vault already holds the real credentials, provisioned out of band
    Given the agent-vault broker has been provisioned out of band with the real Claude OAuth credential and the real GitHub credential
    And the shopsystem-bc-launcher BC is installed
    When bc-container launch is run with BC name "shopsystem-messaging" against the provisioned broker
    Then the brokered Claude OAuth substitution and the brokered GitHub substitution both succeed
    And bc-container launch performed no step that read a real credential from any host file

  @scenario_hash:f1b70c2b9ec76b98 @bc:shopsystem-bc-launcher
  Scenario: bc-container launch never writes a real credential into the broker vault or into a container
    Given the shopsystem-bc-launcher BC is installed
    When bc-container launch is run with BC name "shopsystem-messaging"
    Then launch executes no step that stores a real credential into the broker vault
    And launch executes no step that places a real credential inside the container

  @scenario_hash:e4348b11e0b38d4f @bc:shopsystem-bc-launcher
  Scenario: no real Claude OAuth credential is observable from inside the container
    Given the container "bc-shopsystem-messaging" is running under the agent-vault model
    When the container's filesystem and process environment are searched from inside the container
    Then the real Claude OAuth accessToken value is not present in any file or environment variable
    And the only .credentials.json present has accessToken equal to "__PLACEHOLDER__"

  @scenario_hash:b8f2e121a5fd77ba @bc:shopsystem-bc-launcher
  Scenario: no real GitHub credential and no host gh or gitconfig path is observable from inside the container
    Given the container "bc-shopsystem-messaging" is running under the agent-vault model
    When the container's filesystem and process environment are searched from inside the container
    Then no real GitHub token value is present in any file or environment variable
    And no path mounted from the host's "~/.config/gh" or "~/.gitconfig" is present inside the container

  @scenario_hash:ff1ee370a4462e7d @bc:shopsystem-bc-launcher
  Scenario: the only credential-bearing secret reachable from inside the container is the revocable agent-vault proxy token
    Given the container "bc-shopsystem-messaging" is running under the agent-vault model
    When the credential-bearing secrets reachable from inside the container are enumerated
    Then the only such secret is the agent-vault proxy token used to authenticate to the broker
    And that token grants only proxy substitution and is independently revocable without exposing any brokered credential
