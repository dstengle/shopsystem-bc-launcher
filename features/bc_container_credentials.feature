Feature: bc-container launch propagates host credentials into launched BC containers

  @scenario_hash:f51f21bb8219af1b @bc:shopsystem-bc-launcher
  Scenario: bc-container launch bind-mounts the host's ~/.claude into the launched BC container
  Given the shopsystem-bc-launcher BC is installed
  And the host directory "$HOME/.claude" exists
  And no Docker container named "bc-shopsystem-messaging" is running
  When I run bc-container launch with BC name "shopsystem-messaging"
  Then the command exits zero
  And the FakeDockerDriver records that the docker run command for "bc-shopsystem-messaging" includes a bind mount with source "$HOME/.claude" and target "/home/vscode/.claude"

  @scenario_hash:636ce0c8a761dead @bc:shopsystem-bc-launcher
  Scenario: bc-container launch bind-mounts the host's ~/.config/gh into the launched BC container
  Given the shopsystem-bc-launcher BC is installed
  And the host directory "$HOME/.config/gh" exists
  And no Docker container named "bc-shopsystem-messaging" is running
  When I run bc-container launch with BC name "shopsystem-messaging"
  Then the command exits zero
  And the FakeDockerDriver records that the docker run command for "bc-shopsystem-messaging" includes a bind mount with source "$HOME/.config/gh" and target "/home/vscode/.config/gh"

  @scenario_hash:58b727750607745f @bc:shopsystem-bc-launcher
  Scenario: bc-container launch bind-mounts the host's ~/.gitconfig read-only into the launched BC container at a staging path
  Given the shopsystem-bc-launcher BC is installed
  And the host file "$HOME/.gitconfig" exists
  And no Docker container named "bc-shopsystem-messaging" is running
  When I run bc-container launch with BC name "shopsystem-messaging"
  Then the command exits zero
  And the FakeDockerDriver records that the docker run command for "bc-shopsystem-messaging" includes a read-only bind mount with source "$HOME/.gitconfig" and target "/tmp/host-gitconfig"

  @scenario_hash:93ce008302cf94cb @bc:shopsystem-bc-launcher
  Scenario: bc-container launch copies the staged host gitconfig into the container user's home after start
  Given the shopsystem-bc-launcher BC is installed
  And the host file "$HOME/.gitconfig" exists
  And no Docker container named "bc-shopsystem-messaging" is running
  When I run bc-container launch with BC name "shopsystem-messaging"
  Then the command exits zero
  And the FakeDockerDriver records that an exec_run was issued against "bc-shopsystem-messaging" copying "/tmp/host-gitconfig" to "/home/vscode/.gitconfig"
  And that exec_run is recorded after the docker run for "bc-shopsystem-messaging" and before the tmux new-session exec_run

  @scenario_hash:85202a4076f45fba @bc:shopsystem-bc-launcher
  Scenario: bc-container launch copies .claude.json from the mounted ~/.claude into the container user's home after start
  Given the shopsystem-bc-launcher BC is installed
  And the host file "$HOME/.claude/.claude.json" exists
  And no Docker container named "bc-shopsystem-messaging" is running
  When I run bc-container launch with BC name "shopsystem-messaging"
  Then the command exits zero
  And the FakeDockerDriver records that an exec_run was issued against "bc-shopsystem-messaging" copying "/home/vscode/.claude/.claude.json" to "/home/vscode/.claude.json"
  And that exec_run is recorded after the docker run for "bc-shopsystem-messaging" and before the tmux new-session exec_run

  @scenario_hash:dc9b988512717923 @bc:shopsystem-bc-launcher
  Scenario: bc-container launch defaults credential mount sources to the operator's standard host paths when no overrides are provided
  Given the shopsystem-bc-launcher BC is installed
  And the host directory "$HOME/.claude" exists
  And the host directory "$HOME/.config/gh" exists
  And the host file "$HOME/.gitconfig" exists
  And no Docker container named "bc-shopsystem-messaging" is running
  When I run bc-container launch with BC name "shopsystem-messaging" and no explicit credential path flags
  Then the command exits zero
  And the FakeDockerDriver records that the docker run command for "bc-shopsystem-messaging" includes exactly these three credential bind mounts:
    | source              | target                    | readonly |
    | $HOME/.claude       | /home/vscode/.claude      | false    |
    | $HOME/.config/gh    | /home/vscode/.config/gh   | false    |
    | $HOME/.gitconfig    | /tmp/host-gitconfig       | true     |

  @scenario_hash:4ba55450ec351623 @bc:shopsystem-bc-launcher
  Scenario: bc-container launch fails fast with a clear error when a default host credential source is missing
  Given the shopsystem-bc-launcher BC is installed
  And the host directory "$HOME/.claude" does not exist
  And no Docker container named "bc-shopsystem-messaging" is running
  When I run bc-container launch with BC name "shopsystem-messaging"
  Then the command exits non-zero
  And stderr contains the literal substring "$HOME/.claude"
  And the FakeDockerDriver records that no docker run command was issued for "bc-shopsystem-messaging"
