@bc:shopsystem-bc-launcher @origin:lead-ona9
Feature: bc-container launch delivers "/workspace/.fabro/" via the shop-templates pour and retires the baked fabro-def bundle (lead-ona9)

  The self-contained fabro loop def is delivered EXACTLY as the
  ".claude/skills/" skill-group is: the shop-templates pour run inside the
  container workspace after clone emits "/workspace/.fabro/" (parallel to the
  ".claude/skills/" pour, scenario 75ae95be0ecf1640).  A "--workspace-mount"
  launch SKIPS the pour and presents the committed "/workspace/.fabro/"
  byte-unchanged, exactly as the committed ".claude/skills/" tree is treated.
  The fabro-def is therefore no longer a BAKED delivery surface: it is pruned
  from the packaged wheel (pyproject package-data) and the bc-base image
  (docker/bc-base/Dockerfile), while "src/bc_launcher/assets/fabro-def/"
  remains in the repo as the def SOURCE mirror.

  @scenario_hash:7700eea079ffe1d8 @bc:shopsystem-bc-launcher
  Scenario: bc-container launch runs the shop-templates pour that emits "/workspace/.fabro/" after clone, "--workspace-mount" skips the pour and uses the committed def byte-unchanged, and the baked fabro-def bundle is retired
    Given the shopsystem-bc-launcher BC is installed
    And a BC named "shopsystem-messaging" with a valid repo URL is configured
    And the bc-base image carries the shop-templates binary
    When I run bc-container launch with BC name "shopsystem-messaging"
    And the container has cloned the repository
    Then the shop-templates pour has been run inside the container's workspace directory and has emitted "/workspace/.fabro/" after clone, parallel to the ".claude/skills/" pour (scenario @scenario_hash:75ae95be0ecf1640)
    And when bc-container launch is run with "--workspace-mount" the pour is SKIPPED and the committed "/workspace/.fabro/" is used byte-unchanged, exactly as the committed ".claude/skills/" tree is treated
    And the fabro-def bundle formerly baked from "src/bc_launcher/assets/fabro-def/" is absent from the packaged wheel (pyproject package-data) and the bc-base image (docker/bc-base/Dockerfile) — no longer a baked delivery surface — while the shop-templates pour delivers "/workspace/.fabro/" at launch, the repo source mirror remaining as the def source
