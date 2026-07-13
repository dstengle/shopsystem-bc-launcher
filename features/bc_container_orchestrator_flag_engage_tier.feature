@bc:shopsystem-bc-launcher @origin:adr-050 @origin:adr-058
Feature: bc-container launch --orchestrator {tmux|fabro} selects the engage tier — external agent-free watcher supervisor vs tmux default (lead-cadr S4, lead-1vbw ADR-058 AMENDMENT-3)

  The canonical launch surface is `bc-container launch <bc> --orchestrator
  {tmux|fabro}` with tmux the DEFAULT (superseding S3's off-by-default
  --fabro-path flag, which remains only as a hidden alias). AFTER the
  readiness barrier passes (scenario 34), the engage tier the launcher issues
  is selected by --orchestrator: 'fabro' REPLACES the tmux/claude engage with
  the fabro run-graph entry — lead-1vbw / ADR-058 AMENDMENT-3 corrects that
  entry from the retired infinite `fabro run dispatcher.toml` (a cyclic
  poll->dispatch->wait->poll loop whose per-tick run-graph events accumulated
  UNBOUNDED in the fabro server heap, OOM-bound — lead-01jw) to an EXTERNAL,
  agent-free, message-driven watcher supervisor: EXACTLY ONE long-lived
  per-container fabro server (`fabro server start --foreground --no-web`),
  against which the always-resident `shop-msg watch --bc <name>` fires ONE
  finite `fabro run workflow.fabro` child per inbound message. It requires NO
  launch-time `--work-id` and starts NO tmux `agent` send-keys session and NO
  `claude` on that path (ADR-050 D3, ADR-058 D6); 'tmux' (default) engages via
  the existing tmux send-keys path exactly as scenario 04, starting NO fabro
  server and issuing NO fabro run. ADR-058 also bundles the clone-path
  bootstrap fix (a valid `~/.fabro/settings.toml` server config + run cwd =
  `/workspace/.fabro`) without which the fresh-clone fabro engage never
  bootstraps. Container / credential-proxy / postgres DSN / shop-msg mailbox
  surfaces are IDENTICAL on both paths — only the engage tier differs (ADR-050
  D1/D2 launch parity).

  FIDELITY (test-fidelity-for-image-layer-container-runtime-scenarios): the
  step defs drive the REAL launcher (controller.launch over the
  FakeDockerDriver) and bind to its ACTUAL recorded exec/send-keys calls — the
  fabro-path server-start + watcher supervisor script, the absence of any tmux
  `agent` send-keys / `claude` engage on that path, the tmux-default engage,
  and the launch-parity surfaces — never to a model.

  @scenario_hash:6352660f8b7ce05c @bc:shopsystem-bc-launcher
    Scenario: bc-container launch --orchestrator fabro selects the external watcher engage tier — starting the one long-lived per-container fabro server and NO tmux engage — with tmux/fabro launch parity and requiring no launch-time work id
    Given the shopsystem-bc-launcher BC is installed
    And bc-container launch is run for BC name "shopsystem-messaging" on the fabro orchestrator launch path selected by "--orchestrator fabro" with no "--work-id" supplied
    And the container "bc-shopsystem-messaging" is running with the self-contained fabro def POURED by shop-templates into "/workspace/.fabro/" at launch, not carried on the baked bc-base image (@scenario_hash:d08bac49e20111f2, re-homed to shopsystem-templates), with the started anthropic-oauth-shim and fabro's anthropic "base_url" wired to it (scenario 76, @scenario_hash:9d42e9490702a27f)
    And the launcher's idempotent readiness barrier composing the messaging DB and the agent-vault broker has passed (scenario 34)
    When the engage step the launcher issues on the fabro orchestrator path is inspected structurally, without a live docker daemon, a running fabro server, or a reachable agent-vault
    Then AFTER the readiness barrier passes the launcher engages the EXTERNAL agent-free message-driven watcher supervisor (bc_container_fabro_engage_external_watcher, @scenario_hash:47da82f60bbd47a9) as the fabro-path engage tier, running NO long-lived "fabro run dispatcher.toml" reactive dispatcher on this path
    And the launcher starts EXACTLY ONE long-lived per-container fabro server running "provider=local" in the foreground with no web UI, issuing the argv "fabro server start --foreground --no-web", against which the watcher fires its finite "fabro run workflow.fabro" children (bc_container_fabro_engage_external_watcher, @scenario_hash:728871aca27b0d8f), so the loop runs headless inside the one bc-base container and nothing is orchestrated outside it
    And no "--work-id" is required at the fabro launch interface and any "--work-id" passed on the fabro path is an ignored no-op, exactly like the tmux path which takes no work id at launch, restoring the interface half of launch parity (ADR-058 D6)
    And no tmux "agent" send-keys session and no "claude" engage is started on this path, the engage tier being REPLACED by the fabro watcher run-graph entry rather than added alongside it (ADR-050 D3)
    And the container, credential-proxy, postgres DSN and shop-msg mailbox surfaces are unchanged from the tmux path, only the engage tier differing (ADR-050 D1/D2 launch parity)

  @scenario_hash:ee8f4803eb5342f0
    Scenario: bc-container launch defaults --orchestrator to tmux and leaves the existing tmux engage unchanged, starting no fabro server and issuing no fabro run
    Given the shopsystem-bc-launcher BC is installed
    And bc-container launch is run for BC name "shopsystem-messaging" with no "--orchestrator" flag supplied
    And the launcher's idempotent readiness barrier has passed (scenario 34)
    When the engage step the launcher issues is inspected structurally, without a live docker daemon or a running fabro server
    Then the orchestrator defaults to "tmux", the canonical launch surface being "bc-container launch <bc> --orchestrator {tmux|fabro}" with "tmux" the default, superseding S3's off-by-default "--fabro-path" flag which may remain only as a hidden alias
    And AFTER the readiness barrier passes the launcher engages via the existing tmux "agent" send-keys path exactly as scenario 04 (@scenario_hash:04236074a60ffcd7) pins, unchanged
    And the launcher starts no ephemeral fabro server and issues no "fabro run" on this default path, so the fabro engage replacement is confined to "--orchestrator fabro" (ADR-050 D1 tmux-default launch parity preserved)
