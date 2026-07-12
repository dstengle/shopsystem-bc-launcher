@bc:shopsystem-bc-launcher @origin:lead-915f
Feature: bc-container standup heals a remote-backed beads schema-skew wall by rebuilding a fresh current-schema dolt DB from the committed issues.jsonl and reseeding the remote (lead-915f)

  ADDITIVE to the empty-remote provisioning family
  (bc_container_beads_bootstrap_resilience @scenario_hash:ada742d33c996d34,
  GAP D/E/G/H/I) — retires/supersedes NOTHING.  That family fires when the
  BC's `<bc>-beads` Dolt remote carries NO Dolt data; HERE the remote DOES
  carry Dolt data, just at a SKEWED OLD schema (v32) BEHIND the baked bd's
  CURRENT target (v53), so the clone SUCCEEDS and `bd bootstrap` fails on the
  #4259 migration-refusal instead.

  Observed live (lead-4qqi, fabro launch on a new bc-base): "Bootstrap
  failed ... 21 schema migrations (v32 -> v53) that bd will not auto-apply to
  a remote-backed database (#4259)" — so the BC never onlines.  bd REFUSES to
  auto-apply schema migrations to a remote-backed DB (fork hazard, bd upstream
  #4259).  In-place `bd migrate` is proven DEAD (lead-065a: hard-fails at
  migration 0047 "table not found: wisps").

  The heal, pinned here in the standup's beads-provisioning orchestration:
  on detecting the #4259 refusal, REBUILD a fresh local dolt DB at the baked
  bd's CURRENT schema from the schema-independent committed
  `.beads/issues.jsonl` via `bd init --from-jsonl` (NOT `bd migrate`), taking a
  pre-heal `bd export --all` safety net FIRST, REFUSING for a lead-role beads
  (sole-clone invariant), and durably reseeding the remote via a brokered
  force-push.

  @scenario_hash:dc9a29a746921a14 @bc:shopsystem-bc-launcher
  Scenario: standup reseeds a fresh current-schema dolt DB from the committed issues.jsonl when the remote-backed DB is behind the baked bd's target schema, so the BC onlines with full issue parity
    Given a BC standup clones a remote-backed beads DB whose Dolt data sits at an OLD schema behind the baked bd's CURRENT target schema
    And the baked bd REFUSES to auto-apply schema migrations to that remote-backed DB per fork-hazard bd upstream #4259, so "bd bootstrap" fails and the BC does not reach online
    And the committed ".beads/issues.jsonl" carries a definite issue prefix and a known count of issues
    When the standup's beads-provisioning orchestration runs its schema-skew heal
    Then the heal REBUILDS a fresh local dolt database at the baked bd's CURRENT schema from the committed ".beads/issues.jsonl" via "bd init --from-jsonl", rather than attempting an in-place "bd migrate" that #4259 refuses and lead-065a proved hard-fails at migration 0047
    And after the rebuild "bd ready" exits zero so the BC reaches online WITHOUT manual intervention
    And the rebuilt database's issue count equals the count committed in ".beads/issues.jsonl" so every committed issue is preserved
    And the rebuilt database's schema version equals the baked bd's current target schema version rather than the old remote-backed version

  @scenario_hash:47b74cae983effba @bc:shopsystem-bc-launcher
  Scenario: the schema-skew heal is a no-op when bd is already healthy at the current schema, so re-running the standup makes no destructive change
    Given a BC whose local beads database already reports "bd ready" exit zero at the baked bd's CURRENT target schema, with no #4259 migration-refusal signal present
    When the standup's beads-provisioning schema-skew heal step runs again
    Then the heal detects that bd is already healthy and performs NO rebuild and NO reseed force-push
    And the heal makes no destructive change to the existing local dolt database and leaves its issue count and schema version unchanged
    And the heal step exits zero as an idempotent no-op

  @scenario_hash:df748234563bdedb @bc:shopsystem-bc-launcher
  Scenario Outline: after the local rebuild the standup force-pushes the rebuilt DB to the BC's beads remote through the agent-vault brokered non-interactive dolt-push path, so a subsequent launch bootstrap-adopts the current schema with no re-heal
    Given the standup has locally rebuilt a fresh current-schema dolt database from the committed ".beads/issues.jsonl" and the BC is already online locally
    And the reseed force-push to the BC's beads remote is a history-replacing push that runs "bd dolt push" through the agent-vault broker's MITM-CA / non-interactive dolt-push credential path
    And the brokered dolt-push credential path is "<broker_cred_state>", the same create-bc seed credential gap pinned at lead-tc38 (@scenario_hash:5351a4a8071b594f) and lead-vb6j (@scenario_hash:e3a0ec19298e7ce7) applied to the reseed push
    When the standup runs its remote reseed force-push after the local rebuild
    Then the reseed force-push result is "<push_result>"
    And the BC's beads remote now serves schema "<remote_schema_after>"
    And a SUBSEQUENT launch's "bd bootstrap" adopts the remote schema with re-heal-required "<subsequent_reheal>", so the reseed is durable only once the brokered path is wired

    Examples:
      | broker_cred_state                         | push_result                          | remote_schema_after | subsequent_reheal |
      | wired via agent-vault MITM-CA broker      | push complete                        | current             | no                |
      | unwired, raw dolt push hits the MITM SSL/cred gap | fails on SSL/non-interactive-credential | behind              | yes               |

  @scenario_hash:fbf7480ef25f766c @bc:shopsystem-bc-launcher
  Scenario: the schema-skew heal takes a full pre-heal export before any destructive step and rebuilds from the committed issues.jsonl as the source of truth
    Given the standup's schema-skew heal has detected the remote-backed DB is behind the baked bd's target and is about to rebuild the local database
    When the heal runs its rebuild ordering
    Then the heal FIRST takes a full "bd export --all" capture to a backup path BEFORE any destructive step such as moving aside or removing the broken embedded-Dolt working set
    And the rebuild's authoritative data SOURCE OF TRUTH is the committed ".beads/issues.jsonl", not the pre-heal export, which is retained only as a forensic safety net
    And if the pre-heal export fails because the old database is unreadable, the heal still proceeds from the committed ".beads/issues.jsonl" rather than aborting

  @scenario_hash:5765dd7d175901e3 @bc:shopsystem-bc-launcher
  Scenario Outline: the reseed heal refuses a lead-role beads because the sole-clone invariant holds only for BCs, and proceeds for a BC
    Given a beads database exhibiting the #4259 remote-backed schema-skew refusal whose shop type is "<shop_type>"
    And the reseed heal's force-push is history-replacing and safe only when the container is the SOLE clone of its beads remote, which holds for a BC but NOT for the lead
    When the reseed heal is invoked against that beads database with no lead-override in effect
    Then the heal's action is "<heal_action>" because a history-replacing reseed force-push would discard Dolt history that is not reconstructable from a sole clone when the beads is not sole-clone
    And the heal exit is "<heal_exit>"

    Examples:
      | shop_type | heal_action                                              | heal_exit |
      | bc        | proceeds with the from-JSONL rebuild and reseed          | zero      |
      | lead      | refuses the rebuild and reseed, directing manual migrate | nonzero   |
