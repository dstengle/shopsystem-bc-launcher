@bc:shopsystem-bc-launcher @origin:adr-058
Feature: a fresh clone-path --orchestrator fabro launch provisions the ~/.fabro server config the external watcher engage needs (lead-1vbw ADR-058 AMENDMENT-3)

  The external agent-free watcher supervisor (lead-1vbw) STILL needs the
  clone-path server-config bootstrap the retired dispatcher engage needed: a
  fresh clone-path container has no host-home ~/.fabro, so before starting the
  ONE per-container fabro server the launcher must provision a VALID
  ~/.fabro/settings.toml ([server.auth] methods + 64-hex SESSION_SECRET +
  fabro_dev_+64hex FABRO_DEV_TOKEN) — DISTINCT from the project
  /workspace/.fabro/settings.toml LLM settings — and run the watcher's finite
  `fabro run workflow.fabro` children with cwd=/workspace/.fabro so the poured
  def resolves. Without this the fresh-clone fabro engage crashes at
  "server.auth.methods: field is required" or "workflow not found".

  FIDELITY: the step defs drive the REAL launcher (controller.launch over the
  FakeDockerDriver) and bind to its ACTUAL recorded engage exec — never a model.

  @scenario_hash:402241f3f31cecd9 @bc:shopsystem-bc-launcher
    Scenario: a fresh clone-path --orchestrator fabro launch provisions the ~/.fabro server config and runs fabro from /workspace/.fabro, so the fabro watcher engage bootstraps successfully instead of crashing at server auth or def resolution
    Given the shopsystem-bc-launcher BC is installed
    And bc-container launch is run for BC name "shopsystem-messaging" on the fabro orchestrator launch path selected by "--orchestrator fabro" in a FRESH CLONE-PATH container with NO host-home "~/.fabro" mount and no interactively pre-configured fabro home
    And the container "bc-shopsystem-messaging" has cloned the repo and shop-templates has POURED "/workspace/.fabro/" including the UNCHANGED ADR-051 "workflow.fabro" child def the watcher's finite children run
    And the launcher's idempotent readiness barrier composing the messaging DB and the agent-vault broker has passed (scenario 34)
    When the launcher's recorded fabro watcher engage steps — the server config it provisions, the "fabro server start" argv, and the working directory of the watcher's "fabro run" children — are inspected structurally, without a live docker daemon, a running fabro server, or a reachable agent-vault
    Then BEFORE starting the server the launcher provisions a VALID server config at "~/.fabro/settings.toml" (the file "fabro server start" reads), e.g. by running "fabro install --non-interactive --skip-llm --github-strategy token", and that file contains a "[server.auth]" table with "methods" set, a "SESSION_SECRET" of exactly 64 hexadecimal characters, and a "FABRO_DEV_TOKEN" of the form "fabro_dev_" followed by 64 hexadecimal characters (NOT a bare hex token), so "fabro server start --foreground --no-web" starts successfully rather than dying at "server.auth.methods: field is required"
    And this provisioned "~/.fabro/settings.toml" server config is DISTINCT from "/workspace/.fabro/settings.toml", the PROJECT LLM settings the launcher already writes — the project settings are NOT the server config and do not by themselves satisfy "fabro server start", so the launcher writes BOTH the project "/workspace/.fabro/settings.toml" and the server "~/.fabro/settings.toml"
    And the launcher runs the external watcher engage — whose finite "fabro run workflow.fabro" children fire against the one per-container server — with the working directory set to the project dir "/workspace/.fabro", NOT "/workspace", so fabro resolves the poured "workflow.fabro" rather than failing "workflow not found: /workspace/workflow.fabro"
    And as the observable result a fresh clone-path "--orchestrator fabro" launch REACHES the fabro watcher engage successfully — the in-container fabro server comes up and the watcher's "fabro run" children resolve the poured def — instead of crashing at server auth bootstrap or def resolution as the un-provisioned clone path currently does (ADR-058 bundled fix, lead-l4iw)
