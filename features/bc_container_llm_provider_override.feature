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
