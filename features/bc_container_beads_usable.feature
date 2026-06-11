Feature: bc-container launch leaves beads functionally usable inside the container

  # lead-rply tightens this scenario's prefix-SOURCE premise.  The launcher
  # must ADOPT the prefix the cloned repo's COMMITTED registry already carries
  # (e.g. 'bclaunch' for shopsystem-bc-launcher), NOT derive it from the BC
  # name (which would yield the wrong 'bclauncher').  A freshly cloned BC lands
  # with .beads/issues.jsonl tracked at HEAD but absent from the working tree
  # and an empty Dolt working set; provisioning must materialize the committed
  # registry into the working tree AND import it so the BC boots WRITE-READY.
  #
  # lead-ezzr SUPERSEDES the lead-kjv7 pull+config+import mechanism (old
  # @scenario_hash 2c9e4d7a1b8f6035), which passed green on a fix that was
  # EMPIRICALLY broken.  The functional readiness intent HOLDS — a freshly
  # launched BC must boot WRITE-READY (`bd create`/`bd ready` succeed,
  # `embeddeddolt/` exists, `.beads` vscode-owned) — but the provisioning
  # MECHANISM is now `bd bootstrap`: it imports the git-tracked
  # `.beads/issues.jsonl` (materialized into the worktree first), creates the
  # embedded-Dolt working set, and derives the prefix from the imported
  # registry.  `bd dolt pull` must NOT run first (its empty DB wedges
  # bootstrap into a no-op — the self-inflicted lead-vlsu deadlock), and the
  # launcher must NOT `bd config set issue_prefix` (bd rejects it) or run a
  # separate `bd import` that pre-creates the DB.
  @scenario_hash:2904f3a905567b48 @bc:shopsystem-bc-launcher
  Scenario: after bc-container launch, beads is provisioned by bd bootstrap and boots write-ready
    Given the shopsystem-bc-launcher BC is installed
    And a BC named "shopsystem-bc-launcher" with a valid repo URL is configured
    And the cloned repository's committed beads registry carries the prefix "bclaunch"
    When I run bc-container launch with BC name "shopsystem-bc-launcher"
    And the container has cloned the repository and bd bootstrap has been run inside the workspace directory
    Then the committed beads registry is materialized into the container's working tree
    And bd dolt pull has NOT been run inside the container's workspace directory
    And the container's beads embedded-Dolt working set directory exists
    And the container's .beads directory is owned by vscode
    And bd create run inside the container's workspace directory exits zero and yields a new issue id carrying that prefix
    And bd ready run inside the container's workspace directory exits zero and lists the committed issues
