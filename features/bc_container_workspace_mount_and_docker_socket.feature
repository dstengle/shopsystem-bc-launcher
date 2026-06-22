Feature: bc-container launch workspace-mount and opt-in docker-socket mount

  @scenario_hash:0bc8e4532c04bf72 @bc:shopsystem-bc-launcher
  Scenario: launch with a workspace-mount bind-mounts the host tree as /workspace and skips the clone
    Given the shopsystem-bc-launcher BC is installed
    And an existing host working tree at a path "/host/lead-repo" containing a git repository
    When I run bc-container launch with the workspace-mount option set to "/host/lead-repo" and no repo URL
    And the container starts
    Then the container has a bind mount whose source is the host path "/host/lead-repo" and whose target is "/workspace"
    And no git clone is performed for the launch
    And the container's /workspace is the host tree presented unchanged

  @scenario_hash:9fc84c8424b2a223 @bc:shopsystem-bc-launcher
  Scenario: launch with a workspace-mount does not re-run clone-path provisioning on the live tree
    Given the shopsystem-bc-launcher BC is installed
    And an existing host working tree at a path "/host/lead-repo" with a committed ".beads" registry and poured ".claude/skills"
    When I run bc-container launch with the workspace-mount option set to "/host/lead-repo" and no repo URL
    And the container starts
    Then no bd bootstrap is run against the mounted /workspace
    And no shop-templates re-pour overwrites the mounted ".claude/skills"
    And the mounted /workspace ".beads" registry and ".claude/skills" are byte-unchanged from the host tree after launch

  @scenario_hash:ff370a4e7e9dac5e @bc:shopsystem-bc-launcher
  Scenario: launch mounts the host docker socket only when the opt-in lead-only flag is given
    Given the shopsystem-bc-launcher BC is installed
    When I run bc-container launch with the docker-socket opt-in flag enabled
    And the container starts
    Then the container has a bind mount whose source is the host docker socket "/var/run/docker.sock"
    And docker inspect of the container shows the docker socket mount present

  @scenario_hash:e177655ba09a73fa @bc:shopsystem-bc-launcher
  Scenario: launch mounts no docker socket by default when the opt-in flag is absent
    Given the shopsystem-bc-launcher BC is installed
    When I run bc-container launch without the docker-socket opt-in flag
    And the container starts
    Then the container has no bind mount whose source is the host docker socket "/var/run/docker.sock"
    And docker inspect of the container shows no docker socket mount present
