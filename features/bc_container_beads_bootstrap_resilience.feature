@bc:shopsystem-bc-launcher @origin:lead-5k8c
Feature: bc-container launch bd-bootstrap is bootstrap-resilient and never fatal-strands the container (lead-5k8c)

  The in-container bd-bootstrap step runs AFTER the repo clone but BEFORE the
  tmux/claude agent-start step.  Observed live 2026-06-22 (lead-4qpq fleet
  relaunch): launching shopsystem-bc-launcher reached a healthy cloned
  container with NO agent because bd-bootstrap fatal-failed with
  "dolt clone ...: git remote has no branches: ...; initialize the repository
  with an initial branch/commit first" and the launcher did a FATAL
  early-return BEFORE agent-start, stranding the container.

  This is the SAME strand CLASS as lead-k4k7 (warn-and-continue for the
  shop-templates skill-refresh) but at a DIFFERENT early-return point.  Two
  additive behaviors are pinned here, both authored in this BC's register:

  1. EMPTY-REMOTE PROVISIONING.  When the BC's `<bc>-beads` Dolt remote is
     EMPTY/uninitialized, the bd-bootstrap step must INITIALIZE it
     (init-and-push an initial branch/commit, seeded from the git-tracked
     `.beads/issues.jsonl`) and then provision cleanly, instead of
     fatal-failing the clone.  Additive to the populated-remote pull path
     (lead-held @scenario_hash:f4ebaa3f7559a84a, NOT retired) and strengthens
     the functional-readiness pin (lead-held @scenario_hash:1f1d178bca957fbc)
     on the empty-remote path.

  2. NO PRE-AGENT-START STEP MAY FATAL-STRAND.  Generalizing the lead-k4k7
     warn-and-continue invariant to the bd-bootstrap step: ANY bd-bootstrap
     failure (including an empty remote that could not be seeded) degrades to
     warn-then-proceed-to-agent-start, so a healthy cloned container is NEVER
     left without an agent.  The agent self-heals the tracker via the BC
     session-start beads-health step.

  @scenario_hash:ada742d33c996d34
  Scenario: launch initializes an empty beads dolt remote then provisions beads write-ready
    Given the shopsystem-bc-launcher BC is installed
    And a BC named "shopsystem-bc-launcher" with a valid repo URL is configured
    And the cloned repository's committed beads registry carries the prefix "bclaunch"
    And the BC's beads dolt remote is empty and uninitialized
    When I run bc-container launch with BC name "shopsystem-bc-launcher"
    Then the launch initializes the empty beads dolt remote with an initial branch and commit
    And the launch retries bd bootstrap after seeding the empty remote
    And the container's beads embedded-Dolt working set directory exists
    And bd create run inside the container's workspace directory exits zero and yields a new issue id carrying that prefix
    And the launch still starts the agent

  @scenario_hash:aecde8d40bc5a7d6
  Scenario: a bd-bootstrap failure warns and proceeds to agent-start without fatal-stranding the container
    Given the shopsystem-bc-launcher BC is installed
    And a BC named "shopsystem-bc-launcher" with a valid repo URL is configured
    And the BC's beads dolt remote is empty and uninitialized
    And the launcher's empty-remote seed step fails at runtime
    When I run bc-container launch with BC name "shopsystem-bc-launcher"
    Then the launch warns about the bd bootstrap failure and still starts the agent
    And the launch result is success

  @scenario_hash:90caf5523e7d5ce0 @bc:shopsystem-bc-launcher
  Scenario: standing up a new BC creates its absent beads tracker repo and seeds the dolt remote so bd bootstrap succeeds
    Given a new BC whose shop-name slug is "<bc>" is being stood up under GitHub owner "<owner>"
    And its scaffolded ".beads/config.yaml" "sync.remote" points at "<owner>/<bc>-beads", distinct from the lead's own "<product>-lead-beads"
    And the "<owner>/<bc>-beads" tracker repository does not yet exist
    When the BC-standup flow provisions the new BC's beads tracker and runs "bd bootstrap"
    Then the standup flow creates the absent "<owner>/<bc>-beads" tracker repository with an initial branch and commit
    And the standup flow adds the "<owner>/<bc>-beads" bd dolt remote and seeds it with an initial push so it is not an empty repository with no branches
    And the subsequent "bd bootstrap" for the new BC exits zero instead of failing with "Repository not found" or "git remote has no branches"
    And "bd create" run in the stood-up BC's workspace exits zero and yields a new issue id so its beads tracker is usable for bd-backed gated work

  @scenario_hash:c1abb192dd2a5eae @bc:shopsystem-bc-launcher
  Scenario: the BC-standup beads-tracker provisioning exec carries a GitHub token so gh repo create reaches the agent-vault proxy and the tracker repo is created
    Given a new BC whose shop-name slug is "<bc>" is being stood up under GitHub owner "<owner>"
    And the BC container's agent-vault proxy is wired with HTTPS_PROXY, the broker CA, and the AGENT_VAULT credentials, but no GitHub token is otherwise present in the provisioning exec environment
    When the standup runs its beads-tracker provisioning exec that invokes "gh repo create <owner>/<bc>-beads --private --add-readme"
    Then that provisioning exec's environment sets a non-empty GH_TOKEN placeholder so gh authenticates through the agent-vault proxy instead of exiting non-zero with a "gh auth login" or "populate GH_TOKEN" error
    And the "gh repo create" invocation exits zero and the "<owner>/<bc>-beads" tracker repository exists and is viewable

  @scenario_hash:8ca9508bd7f5fecf @bc:shopsystem-bc-launcher
  Scenario: after standup the new BC's functional bd dolt remote resolves to the derived owner so bd bootstrap clones <owner>/<bc>-beads instead of the ORIGIN_OWNER placeholder
    Given a new BC whose shop-name slug is "<bc>" is stood up from a lead whose GitHub owner resolves to "<owner>"
    And its scaffolded beads tracker config was pushed carrying the literal "ORIGIN_OWNER" placeholder in the tracker remote because no origin owner was known at scaffold time
    When the BC-standup flow provisions the in-container beads tracker and runs "bd bootstrap"
    Then the in-container tracker's functional bd dolt remote, the one "bd dolt remote list" reports and "bd bootstrap" clones from, contains no literal "ORIGIN_OWNER" segment
    And that functional bd dolt remote's owner segment equals the derived GitHub owner "<owner>" so its clone target is "<owner>/<bc>-beads"
    And "bd bootstrap" for the new BC exits zero instead of failing "Repository not found" against an "ORIGIN_OWNER/<bc>-beads" URL

  @scenario_hash:6fc82a7375ed8aa9 @bc:shopsystem-bc-launcher
  Scenario Outline: the standup's empty-remote-seed step fires for an unseeded freshly-created tracker because the empty-remote classifier recognizes the current bc-base dolt "contains no Dolt data" error as well as the legacy "no branches" error
    Given the standup's create-absent orchestration has created the tracker repo "<owner>/<bc>-beads" with "gh repo create --add-readme", so it exists with a git README branch but carries no Dolt refs
    And in that state the in-container "bd bootstrap" fails its Dolt clone with the error text "<bootstrap_error>"
    And the classification under observation is the standup's executable "_is_empty_remote_failure" predicate exercised on that error text, and the seed step under observation is the controller's seed-then-retry block that predicate gates, not a live standup run
    When the standup evaluates whether that "bd bootstrap" failure is an empty/unseeded-remote failure and runs its empty-remote-seed step
    Then the "_is_empty_remote_failure" predicate classifies "<bootstrap_error>" as an empty-remote failure, recognizing the current bc-base dolt "contains no Dolt data" text in addition to the legacy "git remote has no branches" text
    And because the failure is classified as empty-remote, the seed step fires, git-init-and-seeds the tracker's initial Dolt data, and the retried "bd bootstrap" exits zero instead of leaving the tracker unseeded
    And the seed firing is caused specifically by recognizing the current-dolt string, so a legacy-only classifier matching solely "git remote has no branches" would leave the seed unfired on the "contains no Dolt data" error rather than retrying unconditionally

    Examples:
      | owner    | bc                    | bootstrap_error                                                                                 |
      | dstengle | shopsystem-knowledge  | clone failed; remote at that url contains no Dolt data                                           |
      | dstengle | shopsystem-knowledge  | git remote has no branches: ...; initialize the repository with an initial branch/commit first  |
