Feature: bc-container launch leaves beads functionally usable inside the container

  # lead-rply tightens this scenario's prefix-SOURCE premise.  The launcher
  # must ADOPT the prefix the cloned repo's COMMITTED registry already carries
  # (e.g. 'bclaunch' for shopsystem-bc-launcher), NOT derive it from the BC
  # name (which would yield the wrong 'bclauncher').  A freshly cloned BC lands
  # with .beads/issues.jsonl tracked at HEAD but absent from the working tree
  # and an empty Dolt working set; provisioning must materialize the committed
  # registry into the working tree AND import it so the BC boots WRITE-READY.
  @scenario_hash:2c9e4d7a1b8f6035 @bc:shopsystem-bc-launcher
  Scenario: after bc-container launch, beads adopts the repo's committed prefix and boots write-ready
    Given the shopsystem-bc-launcher BC is installed
    And a BC named "shopsystem-bc-launcher" with a valid repo URL is configured
    And the cloned repository's committed beads registry carries the prefix "bclaunch"
    When I run bc-container launch with BC name "shopsystem-bc-launcher"
    And the container has cloned the repository and bd dolt pull has been run inside the workspace directory
    Then the committed beads registry is materialized into the container's working tree
    And the beads issue_prefix configured inside the container's .beads is non-empty and equals the repo's committed prefix
    And bd create run inside the container's workspace directory exits zero and yields a new issue id carrying that prefix
    And bd ready run inside the container's workspace directory exits zero and lists the committed issues
