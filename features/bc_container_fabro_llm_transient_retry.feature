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
