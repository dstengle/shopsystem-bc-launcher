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

  # RETIRED-SCENARIO PROVENANCE (work_id lead-83mh8):
  #   b3054f5439369fa8  superseded-by  4c9f5b265c5098b7
  #   reason: the retired scenario pinned only "active LLM provider = openrouter"
  #     and let lead-ifye3.2 register a CUSTOM [llm.providers.openrouter] fabro
  #     provider, a shape a REAL end-to-end scout proved never completes a
  #     dispatch — fabro's catalog auto-routing resolves "anthropic/..."-prefixed
  #     OpenRouter model strings to the BUILT-IN "anthropic" provider before the
  #     custom "openrouter" provider is considered ("Provider 'anthropic' not
  #     registered").  The corrected scenario below (4c9f5b265c5098b7) pins the
  #     override under fabro's NATIVE "openai" provider identity with base_url
  #     overridden to the OpenRouter endpoint, so catalog routing lands
  #     unambiguously on a registered provider.
  #
  #   14290420156c5ee0  superseded-by  98b956adece2b7e0
  #   reason: the retired scenario pinned the node-side credential env var as a
  #     CUSTOM "OPENROUTER_API_KEY". fabro's startup precondition check (run in
  #     the sandboxed worker) only recognizes ANTHROPIC_API_KEY / OPENAI_API_KEY,
  #     so a custom OPENROUTER_API_KEY never reached that check and the native
  #     openai provider never saw its key. The corrected scenario below
  #     (98b956adece2b7e0) rides fabro's NATIVE "OPENAI_API_KEY" env var
  #     (__PLACEHOLDER__ node-side, no header-reshaping shim), with the
  #     agent-vault broker's MITM proxy substituting the real key on the wire
  #     scoped to the OpenRouter host — the broker-side vault lookup key stays
  #     OPENROUTER_API_KEY and is DECOUPLED from the node-side env var name (the
  #     substitution matches by DESTINATION HOST, not by env var name).
  #
  # RETIRED-SCENARIO PROVENANCE (work_id lead-ifye3.5):
  #   4c9f5b265c5098b7  superseded-by  af07c326a031fafe
  #   reason: the retired scenario pointed the native "openai" provider's base_url
  #     DIRECTLY at "https://openrouter.ai/api/v1", a shape a this-session
  #     root-cause dive proved never completes a real dispatch — fabro's SANDBOXED
  #     node execution clears + FilterSensitive-strips credential-shaped env vars
  #     (*_api_key/*_token/…) AND the sandboxed LLM call never routes through
  #     HTTPS_PROXY, so agent-vault can never substitute the real credential from
  #     inside the sandbox.  The corrected scenario below (af07c326a031fafe) points
  #     the native "openai" provider's base_url at the LOCAL "openrouter-shim"
  #     loopback endpoint (an unsandboxed, container-level reverse proxy launched
  #     with the same launch-lifecycle shape as the existing anthropic-oauth-shim),
  #     moving the real outbound egress — where agent-vault substitutes the
  #     credential — onto the shim's OWN hop instead of the sandboxed node's.
  #
  #   98b956adece2b7e0  superseded-by  05638241a033ef0c
  #   reason: the retired scenario placed the agent-vault substitution on the
  #     SANDBOXED node's OWN outbound wire hop (real key onto the node's
  #     Authorization: Bearer header via the container HTTPS_PROXY).  fabro's
  #     sandboxed execution path CLEARS + FilterSensitive-strips credential-shaped
  #     env vars before spawning AND never routes the sandboxed LLM call through
  #     HTTPS_PROXY, so agent-vault could never substitute the real key from inside
  #     the sandbox.  The corrected scenario below (05638241a033ef0c) moves the
  #     substitution ONE HOP OUT: the node-side "OPENAI_API_KEY" stays the literal
  #     "__PLACEHOLDER__" (carried unchanged onto the "Authorization: Bearer"
  #     header the node sends to the local "openrouter-shim"), and the UNSANDBOXED
  #     "openrouter-shim" process's OWN environment carries the real "HTTPS_PROXY"
  #     through which agent-vault substitutes the real OpenRouter key on the shim's
  #     outbound wire hop, scoped to the OpenRouter host — the GITHUB_TOKEN no-shim
  #     pattern moved one hop out.  work_id lead-ifye3.5.

  @scenario_hash:1d9d3777e3c3d8f5 @bc:shopsystem-bc-launcher
  Scenario: a plain launch with no operator-supplied provider override keeps the Anthropic-subscription path as the active LLM provider
    Given the shopsystem-bc-launcher BC is installed
    And no launch-time "--llm-provider" or "BCLAUNCHER_LLM_PROVIDER" override is supplied
    When bc-container launch is run for BC name "shopsystem-messaging"
    Then the container's fabro run is launched with the active LLM provider set to "anthropic"
    And no OpenRouter agent-vault credential is requested for this launch

  @scenario_hash:af07c326a031fafe @bc:shopsystem-bc-launcher
  Scenario: an explicit launch-time provider override registers fabro's NATIVE "openai" provider identity with its "base_url" pointed at the LOCAL "openrouter-shim" loopback endpoint, not directly at OpenRouter's own host
    Given the shopsystem-bc-launcher BC is installed
    And the operator supplies a launch-time LLM provider override of "openrouter" via "--llm-provider openrouter" (or "BCLAUNCHER_LLM_PROVIDER=openrouter")
    When bc-container launch is run for BC name "shopsystem-messaging" with the operator-supplied provider override
    Then the container's fabro settings register the override under fabro's NATIVE "openai" provider identity, with its "base_url" set to the local "openrouter-shim" process's loopback address — not "https://openrouter.ai" directly and no new custom "openrouter" fabro provider is registered
    And the "openrouter-shim" process is launched as an unsandboxed, container-level process alongside the fabro sandboxed run, the same launch-lifecycle shape the existing "anthropic-oauth-shim" already uses
    And the Anthropic anthropic-oauth-shim path is not engaged for this launch

  @scenario_hash:a28018af66182e33 @bc:shopsystem-bc-launcher
  Scenario: registering any override beyond "base_url" on the openai provider entry breaks fabro's startup precondition gate — only "base_url" may be touched
    Given the shopsystem-bc-launcher BC is installed
    And the operator supplies a launch-time LLM provider override of "openrouter"
    When the container's fabro settings register the "openai" provider override with ONLY "base_url" overridden and no other key changed
    Then the sandboxed worker's startup precondition check passes cleanly and the run proceeds to its first node
    But when an explicit "adapter" or "auth" override is added on top of "base_url" — even a value that would logically merge with the built-in catalog default — the same precondition check instead fails immediately with "No LLM providers configured, set ANTHROPIC_API_KEY or OPENAI_API_KEY", before any node runs

  @scenario_hash:7f55b8ee9e092692 @bc:shopsystem-bc-launcher
  Scenario: the "openrouter-shim" is an unsandboxed, container-level reverse proxy that forwards the sandboxed node's request unchanged to OpenRouter's real API host, with no header reshaping
    Given the shopsystem-bc-launcher BC is installed
    And the operator supplies a launch-time LLM provider override of "openrouter"
    And the "openrouter-shim" process is running, listening on a loopback address only
    When the sandboxed fabro node issues its LLM call to the "openai"-identified provider's configured "base_url"
    Then the request reaches the "openrouter-shim" process over plain loopback, with no "HTTPS_PROXY" needed for that hop
    And the shim forwards the request to "https://openrouter.ai/api" plus the incoming request path, unchanged, with no header reshaping — unlike the "anthropic-oauth-shim", which does reshape headers
    And the shim streams the upstream response back to the sandboxed node unchanged

  @scenario_hash:05638241a033ef0c @bc:shopsystem-bc-launcher
  Scenario: the real OpenRouter credential is substituted on the shim's own outbound hop by agent-vault, matching the GITHUB_TOKEN no-shim pattern moved one hop out — never present in the sandboxed node's filesystem or process environment
    Given the shopsystem-bc-launcher BC is installed
    And the operator supplies a launch-time LLM provider override of "openrouter"
    And an agent-vault broker with a registered OpenRouter-host credential service is running on the shopsystem network and is reachable
    When bc-container launch starts the agent for BC name "shopsystem-messaging" with the OpenRouter provider override
    Then the sandboxed node's "OPENAI_API_KEY" value is the literal placeholder "__PLACEHOLDER__", carried unchanged onto the "Authorization: Bearer" header the node sends to the "openrouter-shim"
    And the "openrouter-shim" process's own environment (not the sandboxed node's) carries the real "HTTPS_PROXY", through which the agent-vault broker's MITM proxy substitutes the real OpenRouter API key onto that same "Authorization: Bearer" header only on the shim's outbound wire hop, scoped to requests directed at the OpenRouter host
    And the real OpenRouter API key is not present in the sandboxed node's filesystem or process environment at any point, including via "[run.environment.env]" overlays, because fabro's sandboxed execution path clears and filters credential-shaped environment variables before spawning

  @scenario_hash:22f2a5bda5c29044 @bc:shopsystem-bc-launcher
  Scenario: bc-launcher resolves each poured node-class placeholder to a literal model ID via fabro run "-I" inputs, sourced from the provider-keyed mapping table for the active provider
    Given the shopsystem-bc-launcher BC is installed
    And the poured "/workspace/.fabro/workflow.fabro" model_stylesheet carries the node-class input placeholders "MODEL_CODING", "MODEL_REVIEW", and "MODEL_DEFAULT"
    And the fleet-wide provider-keyed model mapping table has an OpenRouter row and an Anthropic row, each naming a literal model ID for the "coding", "review", and "default" node-class tiers
    And the operator supplies a launch-time LLM provider override of "openrouter"
    When bc-container launch runs the container's fabro workflow for BC name "shopsystem-messaging" with the OpenRouter provider override
    Then the fabro run command line supplies three "-I" inputs — MODEL_CODING, MODEL_REVIEW, and MODEL_DEFAULT — each set to the literal model ID recorded in the mapping table's OpenRouter row for that node-class
    And when the same launch is run with no provider override, the same three inputs instead carry the literal model IDs recorded in the mapping table's Anthropic row

  @scenario_hash:c99e79ac24f56f5c @bc:shopsystem-bc-launcher
  Scenario: a real dispatch completes end-to-end on a BC launched with the OpenRouter override, with no software release required
    Given the shopsystem-bc-launcher BC is installed
    And an agent-vault broker with a registered OpenRouter credential service is running on the shopsystem network and is reachable
    And the operator supplies a launch-time LLM provider override of "openrouter"
    When bc-container launch is run for a BC with the OpenRouter provider override and a substantive assign_scenarios dispatch is delivered to it
    Then the dispatched work reaches a gated work_done, having executed through at least one non-trivial node-class, such as ".coding", whose model resolved to a literal OpenRouter model ID
    And no software release, BC-base image rebuild, or template re-pour was required to reach this outcome — only the launch-time provider override and a container relaunch
