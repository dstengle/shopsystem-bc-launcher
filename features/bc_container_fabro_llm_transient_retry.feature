@bc:shopsystem-bc-launcher @origin:lead-6ev8
Feature: the fabro LLM/ACP node retries-and-survives a transient 429 burst (lead-6ev8)

  ROOT-CAUSE resilience pin for lead-6ev8 (resolves lead-01jw.3 facet-2). The
  fabro LLM/ACP agent path FAILED-FAST on a transient 429 rate-limit instead of
  retrying: dogfood run 01KXF5XB24R1RXDX4KEESVVC53 (2026-07-14) showed the
  `bc-router classify` LLM node error "Rate limited by anthropic" then a
  content-free `emit_blk` BLOCKED ~14s, despite the oauth-shim showing 200 then
  429x4 (infra sound). The committed workflow.fabro ALREADY carried `retry=4`
  (classify) / `retry=3` (judgment nodes) per lead-i0wi F1 — yet the run showed
  the node running with max_attempts=1.

  EMPIRICAL ROOT CAUSE (reconciled at the REAL fabro v0.254.0 mechanism, not a
  model): `retry=N` is NOT a recognized fabro node attribute — it is silently
  ignored, leaving the node at max_attempts=1 (fail-fast). The recognized
  node-level retry-budget attribute is `max_retries=N`, which the real fabro
  runtime honors as max_attempts = N+1. Proven by running the REAL fabro binary:
  a probe node with `retry=4` reports `stage.started` max_attempts=1, while the
  same node with `max_retries=4` reports max_attempts=5. So `retry=4` on
  classify was load-bearing-but-inert; the fix is to give the LLM/ACP nodes the
  EFFECTIVE `max_retries=N` budget so max_attempts > 1 and a single transient
  429 is not terminal.

  FIDELITY (run the REAL tool, do not reimplement): the retry-and-survive
  SEMANTIC is bound by running the REAL fabro binary over minimal probe graphs
  and observing the emitted `stage.started` max_attempts — with the negative
  control that the pre-fix `retry=N` attribute yields max_attempts=1 (fail-fast)
  while `max_retries=N` yields max_attempts=N+1 (> 1). That proven-effective
  attribute is then required on the committed workflow.fabro LLM/ACP nodes. If
  the fabro binary genuinely cannot be obtained (no network / no server), the
  runtime leg SKIPs honestly rather than papering over a failure. This is NOT a
  model and NOT a shallow string-match. ADDITIVE: references (does not re-pin)
  the lead-01jw.3 diagnostic scenarios and the lead-i0wi retry work.

  @scenario_hash:3b3cf899ddd8ed68 @bc:shopsystem-bc-launcher
  Scenario: a fabro LLM/ACP node survives a transient 429 burst — it retries and completes to a real gated work_done rather than failing-fast to the failsafe on the first transient error
    Given the container "bc-shopsystem-messaging" is running the "--orchestrator fabro" watcher engage with its single long-lived shared per-container fabro server
    And an inbound message carrying a work_id on a scenario path fires one finite "fabro run workflow.fabro" child whose graph reaches an LLM/ACP agent node such as "bc-router classify"
    And the model provider returns a BURST of transient 429 rate-limit responses on that node's first model calls and then returns to serving capacity within the node's retry budget
    When the finite child runs that LLM/ACP node through the transient burst
    Then the LLM/ACP node RETRIES the transient error rather than terminating on the first 429 — its workflow-level retry semantics are max_attempts > 1, so a single transient error is not terminal
    And once the provider returns to capacity within the retry budget, the node SUCCEEDS on a subsequent attempt and the run CONTINUES past that node rather than stopping there
    And the run proceeds to its terminal work_done as a REAL gated outcome — status "complete" for a produced-and-gated deliverable, or a substantive clarify/block from the deliverable path — NOT the content-free failsafe block emitted after ~14s on the first 429 that lead-6ev8 observed
    And the negative control holds: a max_attempts=1 node with no retry budget would have failed-fast to the failsafe on the first 429, which is the lead-6ev8 regression this behavior closes

  @scenario_hash:088460f2fd9490a4 @bc:shopsystem-bc-launcher
  Scenario: the LLM/ACP node's retries use bounded exponential backoff — spaced, count-bounded, and total-wait-bounded — so the client does not amplify the 429 with an immediate retry-storm
    Given the container "bc-shopsystem-messaging" is running the "--orchestrator fabro" watcher engage with its single long-lived shared per-container fabro server
    And a finite "fabro run workflow.fabro" child reaches an LLM/ACP agent node whose model calls are met with repeated transient 429 rate-limit responses
    When the node retries the transient error across successive attempts
    Then the delay BETWEEN successive retry attempts INCREASES from one attempt to the next — exponential backoff — rather than retrying immediately at a fixed zero-or-tiny interval that would hammer the provider
    And the growing backoff delay is CAPPED at a maximum per-attempt ceiling, so the interval increases but does not grow unboundedly
    And the retry count is BOUNDED — the node attempts a finite number of times, not indefinitely — and the cumulative wait across all retries is BOUNDED by a total retry-budget ceiling, so the node cannot hang the run forever waiting on a persistently-unavailable provider
    And because the retries are spaced by increasing backoff rather than fired immediately, the client does not itself amplify the rate-limit into a self-inflicted retry-storm against the shared account

  @scenario_hash:acd8d90bd9d4e4df @bc:shopsystem-bc-launcher
  Scenario: the fabro LLM path's transient-error resilience matches the tmux claude agent's — a transient rate-limit a tmux run survives, a fabro run also survives, and the operator sees the same completion outcome regardless of runtime
    Given a tmux-engaged claude BC processing substantive work hits the same transient 429 rate-limit burst and, via Claude Code's long robust 429 backoff, survives it and drives the work to a real gated completion
    And a fabro-engaged BC processing that same substantive work hits the same transient 429 rate-limit burst on its LLM/ACP node
    When each runtime processes the same substantive work across the same transient rate-limit burst that resolves within a survivable window
    Then the fabro run SURVIVES the transient burst exactly as the tmux run does — it retries with bounded exponential backoff and completes to a real gated work_done — rather than blocking opaquely on the first 429 as the pre-fix fabro run did (lead-6ev8)
    And the operator sees the SAME completion outcome from either runtime for the same survivable transient burst — the work_id reconciles to a real gated result on both — so a transient rate-limit does not decide whether the work gets done based on which runtime ran it
    And this closes the lead-01jw.3 facet-2 gap where tmux completed lead-ew86 (a substantive request_bugfix) while fabro blocked on the identical class of transient 429, because the two runtimes now share the same transient-error resilience posture

  @scenario_hash:591515631f39c311 @bc:shopsystem-bc-launcher
  Scenario: when transient errors PERSIST beyond the retry budget, the run blocks only AFTER a bounded retry effort — the terminal report is the infra-path / rate-limit-429 diagnostic already pinned, not a first-error fail-fast
    Given the container "bc-shopsystem-messaging" is running the "--orchestrator fabro" watcher engage with its single long-lived shared per-container fabro server
    And a finite "fabro run workflow.fabro" child reaches an LLM/ACP agent node whose model provider returns transient 429 rate-limit responses that PERSIST for longer than the node's entire retry budget
    When the node exhausts its bounded retry effort — multiple spaced attempts under exponential backoff — without the provider returning to capacity
    Then the node reaches exhaustion ONLY AFTER that bounded retry effort — the multiple spaced attempts DID occur — rather than terminating on the first transient error as the pre-fix max_attempts=1 path did
    And only at exhaustion does the run block and emit its terminal blocked work_done, whose REPORTING behavior is the infra-path / rate-limit-429 DIAGNOSTIC already pinned by fabro_diagnostic_blocked_work_done (@scenario_hash:738f35759127fe7f Examples row reason_class "infra-path" / detail_marker "rate-limit-429", carrying the failing node and captured context per @scenario_hash:629be1e0224f3a03) — this scenario does NOT re-pin that reporting behavior, it references it by value
    And the distinction this pins is temporal-and-behavioral: the diagnostic block is now the END of a bounded retry effort, not the response to a single transient error, so exhaustion is a genuine capacity failure rather than a fail-fast
