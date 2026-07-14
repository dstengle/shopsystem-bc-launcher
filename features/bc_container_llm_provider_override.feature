@bc:shopsystem-bc-launcher @origin:lead-ifye3.2
Feature: a launch-time --llm-provider / BCLAUNCHER_LLM_PROVIDER override selects the active LLM provider for the container's fabro run, defaulting to the Anthropic subscription path

  Today the launcher wires exactly one LLM provider — Anthropic, via the
  in-container anthropic-oauth-shim — with no notion of a selectable "active
  LLM provider". This feature introduces provider RESOLUTION: the launch
  resolves an active LLM provider (default "anthropic") and threads it into
  the container's fabro run, so a later launch-time "--llm-provider" /
  "BCLAUNCHER_LLM_PROVIDER" override can select a different provider
  (openrouter) with a no-shim agent-vault credential and a provider-keyed
  model mapping — all without a software release.

  This file is grown behavior-by-behavior under lead-ifye3.2. Behavior 1
  (below) pins the Anthropic DEFAULT of the resolution and the ABSENCE of any
  OpenRouter credential on the plain-launch path; behaviors 2-5 append their
  scenarios (the openrouter override, the no-shim OpenRouter credential, the
  provider-keyed model mapping, and the end-to-end dispatch) to THIS feature.

  FIDELITY: the step defs drive the REAL launcher (controller.launch over the
  FakeDockerDriver) and bind to its ACTUAL recorded fabro engage exec — never
  a model, never a shallow string-match.

  @scenario_hash:1d9d3777e3c3d8f5 @bc:shopsystem-bc-launcher
  Scenario: a plain launch with no operator-supplied provider override keeps the Anthropic-subscription path as the active LLM provider
    Given the shopsystem-bc-launcher BC is installed
    And no launch-time "--llm-provider" or "BCLAUNCHER_LLM_PROVIDER" override is supplied
    When bc-container launch is run for BC name "shopsystem-messaging"
    Then the container's fabro run is launched with the active LLM provider set to "anthropic"
    And no OpenRouter agent-vault credential is requested for this launch

  @scenario_hash:b3054f5439369fa8 @bc:shopsystem-bc-launcher
  Scenario: an explicit launch-time provider override selects OpenRouter, winning over the Anthropic default
    Given the shopsystem-bc-launcher BC is installed
    And the operator supplies a launch-time LLM provider override of "openrouter" via "--llm-provider openrouter" (or "BCLAUNCHER_LLM_PROVIDER=openrouter")
    When bc-container launch is run for BC name "shopsystem-messaging" with the operator-supplied provider override
    Then the container's fabro run is launched with the active LLM provider set to "openrouter"
    And the Anthropic anthropic-oauth-shim path is not engaged for this launch

  @scenario_hash:14290420156c5ee0 @bc:shopsystem-bc-launcher
  Scenario: the OpenRouter credential rides a new agent-vault-brokered credential with no header-reshaping shim, matching the GITHUB_TOKEN no-shim pattern rather than the Anthropic oauth-shim pattern
    Given the shopsystem-bc-launcher BC is installed
    And the operator supplies a launch-time LLM provider override of "openrouter"
    And an agent-vault broker with a registered OpenRouter credential service is running on the shopsystem network and is reachable
    When bc-container launch starts the agent for BC name "shopsystem-messaging" with the OpenRouter provider override
    Then the node-side "OPENROUTER_API_KEY" value is the literal placeholder "__PLACEHOLDER__", with no header-reshaping shim process launched for the OpenRouter path
    And the agent-vault broker's MITM proxy substitutes the real OpenRouter API key onto the outbound "Authorization: Bearer" header only on the wire
    And the real OpenRouter API key is not present in the container's filesystem or process environment
