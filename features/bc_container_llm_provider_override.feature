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
  #
  #   22f2a5bda5c29044  superseded-by  a3b2b6bebcee78f5
  #   reason: the retired scenario resolved each poured node-class placeholder to a
  #     literal model ID via three per-child fabro-run "-I MODEL_CODING/REVIEW/
  #     DEFAULT" inputs feeding the workflow.fabro model_stylesheet's
  #     "{{ inputs.MODEL_* }}" templating.  fabro >= v0.267.0 (the FABRO_VERSION the
  #     openrouter base_url override depends on) removed model_stylesheet templating
  #     outright (fabro commit 911e080f3, "Limit DOT templates to prompt + goal"):
  #     "{{ inputs.X }}" inside model_stylesheet becomes literal, unparseable text —
  #     a HARD PARSE ERROR, so the "-I MODEL_*" inputs can no longer resolve any
  #     per-node-class model.  Per explicit product-authority direction, per-node-
  #     class model differentiation is DEPRIORITIZED (not permanently dropped) in
  #     favor of a proven run-wide "fabro run --model <resolved-literal> --provider
  #     <active>" flag pair on the dispatcher's per-child spawn command (scout-proven
  #     with ZERO model_stylesheet in the graph: real OpenRouter response, both
  #     nodes).  The corrected scenario below (a3b2b6bebcee78f5) pins the run-wide
  #     "--model/--provider" flags REPLACING the retired per-node-class "-I MODEL_*"
  #     inputs; the fleet-wide provider-keyed model mapping table (ADR-063) is
  #     UNCHANGED as the lookup structure — only what bc-launcher does with the
  #     resolved value changes.  work_id lead-ifye3.5.
  #
  #   c99e79ac24f56f5c  superseded-by  76badc67216f0d91
  #   reason: the retired end-to-end capstone still asserted the completion through
  #     the REMOVED model_stylesheet "{{ inputs.MODEL_CODING }}" templating and the
  #     retired per-node-class "-I MODEL_*" inputs (both gone after a3b2b6bebcee78f5:
  #     fabro >= v0.267.0 removed model_stylesheet templating outright), so its
  #     ".coding"-resolves-via-stylesheet binding could no longer hold.  The
  #     corrected scenario below (76badc67216f0d91) pins the same end-to-end outcome
  #     against the CORRECTED architecture: the completion now runs through the
  #     UNSANDBOXED "openrouter-shim" with the run-wide "--model/--provider" pair,
  #     and the ".coding" node's model resolves to a literal OpenRouter model ID via
  #     the shim (resolve_run_wide_model / the ADR-063 mapping table), NOT the
  #     removed stylesheet.  The one-time FABRO_VERSION native-"[llm.providers.
  #     openai]"-support image precondition (an out-of-scope Architect-level infra
  #     action) is stated as an ALREADY-SATISFIED Given, prior to and independent of
  #     this launch — so reaching the outcome needs NO further software release,
  #     BC-base image rebuild, or template re-pour beyond that satisfied precondition,
  #     only the launch-time provider override + a container relaunch.
  #     work_id lead-ifye3.5.
  #
  # RETIRED-SCENARIO PROVENANCE (work_id lead-6tu6o.1):
  #   76badc67216f0d91  superseded-by  1cee6978cbf9ac53
  #   reason: the retired capstone asserted that "no further software release,
  #     BC-base image rebuild, or template re-pour beyond the already-satisfied
  #     FABRO_VERSION image precondition was required" to reach a gated work_done
  #     for its OWN nested "bc-container launch" When-clause.  That claim is FALSE,
  #     confirmed twice: (1) a live-proof attempt (lead-85s41) hit
  #     "FileNotFoundError: 'docker'" inside the running container; (2) a router-run
  #     Architect dispatch (lead-6tu6o) inspected the bc-base image directly against
  #     its confirmed digest and found NO docker binary baked in at all.  On this
  #     image's Debian trixie base the "docker.io" apt package installs only the
  #     daemon (dockerd/docker-proxy/docker-init) and never the client; the separate
  #     "docker-cli" package (client-only, same 26.1.5+dfsg1-9+b13 version) is what
  #     produces a working /usr/bin/docker.  The corrected scenario below
  #     (1cee6978cbf9ac53) keeps the acceptance bar UNCHANGED (a gated work_done, a
  #     real OpenRouter model resolved via the "openrouter-shim") and corrects only
  #     the precondition claim: it names ONE additional already-satisfied bc-base
  #     image-build precondition — the "docker-cli" apt package baked into
  #     docker/bc-base/Dockerfile, alongside the FABRO_VERSION pin — and names the
  #     "--mount-docker-socket" operator launch flag as a precondition Given.  Per
  #     this file's existing convention the Dockerfile apt-package edit itself stays
  #     an Architect-level infra action rather than scenario content: only the
  #     EXISTENCE of the precondition is pinned.  FLEET-WIDE BLAST RADIUS (ADR-021
  #     D1/D3, accepted tradeoff): shopsystem-bc-base is a SINGLE image with one
  #     floating ":latest" tag and no per-BC override (ADR-021 D4, deferred), so
  #     baking docker-cli reaches EVERY BC container fleet-wide.
  #     work_id lead-6tu6o.1.
  #
  # RETIRED-SCENARIO PROVENANCE (work_id lead-ifye3.12):
  #   1cee6978cbf9ac53  superseded-by  5d49031bab379ba6
  #   reason: the retired capstone asserted that "no further software release was
  #     required beyond the already-satisfied FABRO_VERSION and bc-base "docker-cli"
  #     image preconditions".  That claim is FALSE — disproven a THIRD time in this
  #     lineage by lead-lp4us's live end-to-end verification.  With BOTH of those
  #     named preconditions genuinely satisfied for the FIRST time, a real nested
  #     launch did reach the "openrouter-shim" and drove 11 real HTTP-200 OpenRouter
  #     completions — and the dispatch STILL could not complete, because TWO further
  #     software fixes remain, not zero:
  #       (A) shopsystem-templates' poured "templates/fabro/workflow.fabro" still
  #           ships the retired model_stylesheet "{{ inputs.X }}" placeholder shape,
  #           which fabro >= v0.267.0-nightly.0 hard-parse-errors on.  Dispatched
  #           separately as lead-ifye3.6, against shopsystem-templates — NOT this BC.
  #       (B) this BC's OWN dispatcher passes the ACTIVE-provider NAME ("openrouter")
  #           to "fabro run --provider" instead of the REGISTERED fabro provider
  #           identity ("openai").  Dispatched separately as lead-ifye3.10, against
  #           this BC.
  #     The corrected scenario below (5d49031bab379ba6) keeps the acceptance bar and
  #     the behavioral shape UNCHANGED (a real dispatch reaching a gated work_done on
  #     a BC launched with the OpenRouter override, a non-trivial ".coding" node-class
  #     model resolved to a literal OpenRouter model ID via the "openrouter-shim") and
  #     corrects ONLY the precondition claim: it names BOTH remaining preconditions
  #     (A) and (B) explicitly in its Given clauses, and its corrected "no further
  #     release" Then-clause enumerates all FOUR preconditions — FABRO_VERSION,
  #     bc-base "docker-cli", the shop-templates model_stylesheet pour-fix, and the
  #     bc-launcher provider-identity call-site-fix.
  #     Per this file's existing convention (the FABRO_VERSION and "docker-cli"
  #     precedents) both (A) and (B) stay out-of-scope actions owned by their OWN
  #     dispatches: only the EXISTENCE of each precondition is pinned here, never its
  #     edit.  Neither (A) nor (B) had landed as of this retirement, so the live
  #     end-to-end proof of 5d49031bab379ba6 is deliberately NOT re-attempted here;
  #     the Architect initiates that retry once lead-ifye3.6 and lead-ifye3.10 both
  #     report work_done.
  #     work_id lead-ifye3.12.
  #
  # RETIRED-SCENARIO PROVENANCE (work_id lead-ifye3.13):
  #   a3b2b6bebcee78f5  superseded-by  bb4f75cea78091c0
  #   reason: the retired scenario's Then-clause pinned the BROKEN value verbatim —
  #     'the child "fabro run" command line carries "--model <literal-model-id>
  #     --provider openrouter", sourced from the mapping table for the active
  #     provider'.  That clause pinned the operator-facing ACTIVE-provider NAME
  #     ("openrouter") onto the "--provider" flag, which fabro resolves by LITERAL
  #     provider lookup.  It therefore contradicted the live af07c326a031fafe, which
  #     pins verbatim that "no new custom \"openrouter\" fabro provider is
  #     registered" — so "--provider openrouter" named a provider that, by that same
  #     live pin, cannot exist.  No reading satisfied both.  a28018af66182e33 closes
  #     the only escape hatch (registering a second "openrouter" entry breaks fabro's
  #     startup precondition gate).  Confirmed against reality by lead-lp4us's live
  #     verification: the config that drove 11 real HTTP-200 OpenRouter completions
  #     carries "--provider openai", not "--provider openrouter".
  #     The successor (bb4f75cea78091c0) keeps the acceptance bar and the behavioral
  #     shape UNCHANGED — run-wide "--model"/"--provider" flags replacing the retired
  #     per-node-class "-I MODEL_*" inputs — and corrects ONLY the provider VALUE and
  #     its sourcing: it names fabro's REGISTERED native identity ("openai", the
  #     "[llm.providers.openai]" entry the override registers) and disentangles
  #     model-sourcing from provider-sourcing, since "sourced from the mapping table"
  #     correctly describes the MODEL only — the provider is sourced from the
  #     registered identity, never from the mapping table.
  #     The defect this retirement unblocks (this BC's dispatcher passing the active
  #     provider NAME to "fabro run --provider" instead of the REGISTERED identity)
  #     was fixed under lead-ifye3.10 and lands in this same commit range; the
  #     successor's wording is the BC's own proposal, ratified verbatim by the PO in
  #     brief-021 §14.
  #     work_id lead-ifye3.13.

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

  @scenario_hash:bb4f75cea78091c0 @bc:shopsystem-bc-launcher
  Scenario: the dispatcher's per-child "fabro run" command line carries run-wide "--model"/"--provider" flags, replacing the retired per-node-class "-I MODEL_CODING"/"MODEL_REVIEW"/"MODEL_DEFAULT" inputs
    Given the shopsystem-bc-launcher BC is installed
    And the operator supplies a launch-time LLM provider override of "openrouter"
    And the fleet-wide provider-keyed model mapping table names a literal model ID for the active provider
    When bc-container launch's dispatcher spawns a child "fabro run" for BC name "shopsystem-messaging" with the OpenRouter provider override
    Then the child "fabro run" command line carries "--model <literal-model-id> --provider openai", the model sourced from the mapping table for the active provider and the provider naming fabro's REGISTERED native identity — the "[llm.providers.openai]" entry the override registers — never the operator-facing "openrouter" provider name, which fabro's literal provider lookup cannot resolve
    And the command line carries no "-I MODEL_CODING=", "-I MODEL_REVIEW=", or "-I MODEL_DEFAULT=" input for this launch
    And every node in the workflow, regardless of its ".coding"/".review"/"*" node-class, resolves to that same single run-wide model — per-node-class model differentiation is not supplied by this launch

  @scenario_hash:5d49031bab379ba6 @bc:shopsystem-bc-launcher
  Scenario: a real dispatch completes end-to-end on a BC launched with the OpenRouter override, given already-satisfied FABRO_VERSION, bc-base "docker-cli", shop-templates model_stylesheet pour, and bc-launcher provider-identity call-site preconditions, with no further software release required
    Given the shopsystem-bc-launcher BC's container image was already built from a bc-base image pinned to a FABRO_VERSION carrying native "[llm.providers.openai]" support, satisfied once, prior to and independent of this launch
    And the "openrouter-shim" process is part of that same already-built image
    And that same bc-base image also bakes in the "docker-cli" apt package (the docker CLI client binary — not satisfied by "docker.io" alone, which on this image's Debian trixie base installs only the "dockerd" daemon, no client), satisfied once, prior to and independent of this launch, so the launched container can perform the nested "bc-container launch" its own dispatched work requires
    And the container is launched with the "--mount-docker-socket" operator flag, so the baked "docker-cli" client has a socket to reach
    And shopsystem-templates' poured "templates/fabro/workflow.fabro" no longer carries the retired "model_stylesheet" "{{ inputs.X }}" placeholder shape, which fabro >= v0.267.0-nightly.0 hard-parse-errors on, satisfied once, prior to and independent of this launch
    And the shopsystem-bc-launcher dispatcher's per-child "fabro run --provider" construction passes the REGISTERED fabro provider identity ("openai"), not the active-provider name ("openrouter"), satisfied once, prior to and independent of this launch
    And an agent-vault broker with a registered OpenRouter-host credential service is running on the shopsystem network and is reachable
    And the operator supplies a launch-time LLM provider override of "openrouter"
    When bc-container launch is run for a BC with the OpenRouter provider override and a substantive assign_scenarios dispatch is delivered to it
    Then the dispatched work reaches a gated work_done, having executed through at least one non-trivial node-class, such as ".coding", whose model resolved to a literal OpenRouter model ID via the "openrouter-shim"
    And no further software release was required beyond the already-satisfied FABRO_VERSION, bc-base "docker-cli", shop-templates model_stylesheet pour-fix, and bc-launcher provider-identity call-site-fix preconditions — only the launch-time provider override, the "--mount-docker-socket" flag, and a container relaunch
