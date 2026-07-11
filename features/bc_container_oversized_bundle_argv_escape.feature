@bc:shopsystem-bc-launcher @origin:lead-m4zt
Feature: an oversized content blob is placed off the docker argv so no E2BIG blocks BC bring-up (lead-m4zt)

  When the launcher brings a BC online it must place a large content blob —
  the fabro def-bundle on the fabro path, or the startup prompt on the tmux
  path — into the running container so the agent loop engages. The defect this
  pins is that the blob was carried as a SINGLE argv element to docker
  exec/run. The Linux per-argument limit MAX_ARG_STRLEN is 128 KiB PER SINGLE
  ARGUMENT (independent of ARG_MAX ~2 MiB for the whole argv+env), so a blob
  larger than 128 KiB fails the spawn with OSError Errno 7 E2BIG ("Argument
  list too long: docker") even though the total environment is tiny. The
  container starts (sleep infinity) but no agent loop process spawns inside, so
  `shop-msg bc-status` stays offline. Splitting/shrinking env or raising
  ARG_MAX does not help — this is a per-arg kernel limit; the blob must leave
  the argv entirely (docker cp to a file, or stream via the process's STDIN).

  This tightens unpinned existing behavior additively: it pins the placement
  MECHANISM (blob off argv) while preserving every already-pinned bring-up
  OUTCOME. Both placement sites are pinned by behavior — the tmux engage path
  and the fabro orchestrator engage path.

  @scenario_hash:b992c459da9914bb
  Scenario: an oversized startup prompt is injected off the docker argv so the tmux-path BC comes online without E2BIG
    Given the shopsystem-bc-launcher BC is installed
    And the docker exec boundary enforces the 128 KiB Linux per-single-argument limit
    When bc-container launch injects a startup prompt larger than 128 KiB on the tmux engage path
    Then no single docker argument carries the oversized startup prompt, so the launch raises no E2BIG "Argument list too long" at the exec boundary
    And the oversized startup prompt is committed to the running agent so the BC comes online

  @scenario_hash:57b4f8ed02a33516
  Scenario: an oversized fabro def-bundle is placed off the docker argv so the fabro-path BC comes online without E2BIG
    Given the shopsystem-bc-launcher BC is installed
    And the docker exec boundary enforces the 128 KiB Linux per-single-argument limit
    When bc-container launch places the fabro def-bundle on the fabro orchestrator engage path
    Then no single docker argument carries the fabro def-bundle blob, so the launch raises no E2BIG "Argument list too long" at the exec boundary
    And the fabro orchestrator engage is started so the BC comes online
