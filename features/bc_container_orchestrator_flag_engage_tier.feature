@bc:shopsystem-bc-launcher @origin:adr-050 @origin:adr-058
Feature: bc-container launch --orchestrator {tmux|fabro} selects the engage tier — fabro reactive dispatcher vs tmux default (lead-cadr S4, lead-odd9 ADR-058)

  The canonical launch surface is `bc-container launch <bc> --orchestrator
  {tmux|fabro}` with tmux the DEFAULT (superseding S3's off-by-default
  --fabro-path flag, which remains only as a hidden alias). AFTER the
  readiness barrier passes (scenario 34), the engage tier the launcher issues
  is selected by --orchestrator: 'fabro' REPLACES the tmux/claude engage with
  the fabro run-graph entry — ADR-058 corrects that entry from the prior
  ONE-SHOT `fabro run workflow.fabro -I BC_NAME -I WORK_ID` (now retired) to
  ONE persistent, reactive-cyclic
  `fabro run dispatcher.fabro -I BC_NAME=<bc>` (ephemeral in-container `fabro
  server start --foreground --no-web` first), requiring NO launch-time
  `--work-id` and starting NO tmux `agent` send-keys session and NO `claude`
  on that path (ADR-050 D3, ADR-058 D1/D6); 'tmux' (default) engages via the
  existing tmux send-keys path exactly as scenario 04, starting NO fabro
  server and issuing NO fabro run. ADR-058 also bundles the clone-path
  bootstrap fix (a valid `~/.fabro/settings.toml` server config + run cwd =
  `/workspace/.fabro`) without which the fresh-clone fabro engage never
  bootstraps. Container / credential-proxy / postgres DSN / shop-msg mailbox
  surfaces are IDENTICAL on both paths — only the engage tier differs (ADR-050
  D1/D2 launch parity).

  FIDELITY (test-fidelity-for-image-layer-container-runtime-scenarios): the
  step defs drive the REAL launcher (controller.launch over the
  FakeDockerDriver) and bind to its ACTUAL recorded exec/send-keys calls — the
  fabro-path server-start + run argv, the absence of any tmux `agent`
  send-keys / `claude` engage on that path, the tmux-default engage, and the
  launch-parity surfaces — never to a model.

  @scenario_hash:a6bb4ad0512f2b11 @bc:shopsystem-bc-launcher
    Scenario: bc-container launch --orchestrator fabro starts the ephemeral in-container fabro server and runs ONE persistent reactive dispatcher def as the engage step, requiring no launch-time work id and running no tmux engage on that path
    Given the shopsystem-bc-launcher BC is installed
    And bc-container launch is run for BC name "shopsystem-messaging" on the fabro orchestrator launch path selected by "--orchestrator fabro" with no "--work-id" supplied
    And the container "bc-shopsystem-messaging" is running with the self-contained fabro def POURED by shop-templates into "/workspace/.fabro/" at launch, not carried on the baked bc-base image (@scenario_hash:d08bac49e20111f2, re-homed to shopsystem-templates), with the started anthropic-oauth-shim and fabro's anthropic "base_url" wired to it (scenario 76, @scenario_hash:9d42e9490702a27f)
    And the launcher's idempotent readiness barrier composing the messaging DB and the agent-vault broker has passed (scenario 34)
    When the engage step the launcher issues on the fabro orchestrator path is inspected structurally, without a live docker daemon, a running fabro server, or a reachable agent-vault
    Then AFTER the readiness barrier passes the launcher starts an ephemeral in-container fabro server running "provider=local" in the foreground with no web UI bound to a local 127.0.0.1 socket, issuing the argv "fabro server start --foreground --no-web", so the loop runs headless inside the one bc-base container and nothing is orchestrated outside it
    And the launcher invokes "fabro run dispatcher.toml -I BC_NAME=shopsystem-messaging" against that server as the ONE persistent engage step, carrying only the constant BC_NAME into the run via the def's "[run.environment.env]" and supplying NO "-I WORK_ID", so the reactive dispatcher def poured into "/workspace/.fabro/" owns the container's lifecycle and discovers work ids at runtime rather than running one-shot on a launch-time work id (ADR-058 D1 correcting ADR-050 D3)
    And no "--work-id" is required at the fabro launch interface and any "--work-id" passed on the fabro path is an ignored no-op, exactly like the tmux path which takes no work id at launch, restoring the interface half of launch parity (ADR-058 D6)
    And no tmux "agent" send-keys session and no "claude" engage is started on this path, the engage tier being REPLACED by the fabro run-graph entry rather than added alongside it (ADR-050 D3)
    And the container, credential-proxy, postgres DSN and shop-msg mailbox surfaces are unchanged from the tmux path, only the engage tier differing (ADR-050 D1/D2 launch parity)

  @scenario_hash:a4726855a22f83d3 @bc:shopsystem-bc-launcher
    Scenario: a fresh clone-path --orchestrator fabro launch provisions the ~/.fabro server config and runs fabro from /workspace/.fabro, so the fabro engage bootstraps successfully instead of crashing at server auth or def resolution
    Given the shopsystem-bc-launcher BC is installed
    And bc-container launch is run for BC name "shopsystem-messaging" on the fabro orchestrator launch path selected by "--orchestrator fabro" in a FRESH CLONE-PATH container with NO host-home "~/.fabro" mount and no interactively pre-configured fabro home
    And the container "bc-shopsystem-messaging" has cloned the repo and shop-templates has POURED "/workspace/.fabro/" including "dispatcher.fabro" and the UNCHANGED ADR-051 "workflow.fabro" child def
    And the launcher's idempotent readiness barrier composing the messaging DB and the agent-vault broker has passed (scenario 34)
    When the launcher's recorded fabro engage steps — the server config it provisions, the "fabro server start" argv, and the working directory of the "fabro run" engage — are inspected structurally, without a live docker daemon, a running fabro server, or a reachable agent-vault
    Then BEFORE starting the server the launcher provisions a VALID server config at "~/.fabro/settings.toml" (the file "fabro server start" reads), e.g. by running "fabro install --non-interactive --skip-llm --github-strategy token", and that file contains a "[server.auth]" table with "methods" set, a "SESSION_SECRET" of exactly 64 hexadecimal characters, and a "FABRO_DEV_TOKEN" of the form "fabro_dev_" followed by 64 hexadecimal characters (NOT a bare hex token), so "fabro server start --foreground --no-web" starts successfully rather than dying at "server.auth.methods: field is required"
    And this provisioned "~/.fabro/settings.toml" server config is DISTINCT from "/workspace/.fabro/settings.toml", the PROJECT LLM settings the launcher already writes — the project settings are NOT the server config and do not by themselves satisfy "fabro server start", so the launcher writes BOTH the project "/workspace/.fabro/settings.toml" and the server "~/.fabro/settings.toml"
    And the launcher issues the persistent "fabro run dispatcher.toml -I BC_NAME=shopsystem-messaging" engage with its working directory set to the project dir "/workspace/.fabro", NOT "/workspace", so fabro resolves the poured "dispatcher.toml" (and the "dispatcher.fabro" / "workflow.fabro" it applies) rather than failing "workflow not found: /workspace/dispatcher.toml"
    And as the observable result a fresh clone-path "--orchestrator fabro" launch REACHES the fabro engage successfully — the in-container fabro server comes up and the "fabro run" engage resolves the poured def — instead of crashing at server auth bootstrap or def resolution as the un-provisioned clone path currently does (ADR-058 bundled fix, lead-l4iw)

  @scenario_hash:24d94274b9cbc2b0 @bc:shopsystem-bc-launcher
  Scenario: the fabro engage invokes "fabro run dispatcher.toml" so the local provider applies and node execution runs in-process, with a negative control that running the bare ".fabro" falls to the docker sandbox
    Given the shopsystem-bc-launcher BC is installed
    And bc-container launch is run for BC name "shopsystem-messaging" on the fabro orchestrator launch path selected by "--orchestrator fabro"
    And the container "bc-shopsystem-messaging" is running with the self-contained fabro def set POURED by shop-templates into "/workspace/.fabro/", including both the "dispatcher.toml" entrypoint and the "dispatcher.fabro" graph def it applies, and the bc-base container has NO docker daemon reachable at "/var/run/docker.sock"
    And the launcher's idempotent readiness barrier composing the messaging DB and the agent-vault broker has passed (scenario 34)
    When the engage the launcher issues and the poured "dispatcher.toml" entrypoint are inspected structurally, without a live docker daemon, a running fabro server, or a reachable agent-vault
    Then AFTER the readiness barrier passes the engage the launcher issues invokes "fabro run dispatcher.toml" — the ".toml" entrypoint, NOT the bare "dispatcher.fabro" graph def — so the run enters through the ".toml" rather than the ".fabro" directly
    And the poured "dispatcher.toml" applies "provider = local" so the fabro sandbox comes up IN-PROCESS in the bc-base container ("Sandbox: local ready") and every native node of the dispatcher graph executes in-process with no docker sandbox and no connection attempt to "/var/run/docker.sock"
    And as the negative control, had the engage instead run the bare "fabro run dispatcher.fabro" (the ".fabro" graph def DIRECTLY), the run would BYPASS the "[environments.local]" provider, DEFAULT to the docker-sandbox executor, and — because the bc-base container has no docker daemon — fail in 0s connecting to "/var/run/docker.sock" and EXIT before the dispatcher ever watches the inbox, which is the exact pre-fix offline failure this ".toml"-entrypoint engage exists to avoid

  @scenario_hash:ee8f4803eb5342f0
    Scenario: bc-container launch defaults --orchestrator to tmux and leaves the existing tmux engage unchanged, starting no fabro server and issuing no fabro run
    Given the shopsystem-bc-launcher BC is installed
    And bc-container launch is run for BC name "shopsystem-messaging" with no "--orchestrator" flag supplied
    And the launcher's idempotent readiness barrier has passed (scenario 34)
    When the engage step the launcher issues is inspected structurally, without a live docker daemon or a running fabro server
    Then the orchestrator defaults to "tmux", the canonical launch surface being "bc-container launch <bc> --orchestrator {tmux|fabro}" with "tmux" the default, superseding S3's off-by-default "--fabro-path" flag which may remain only as a hidden alias
    And AFTER the readiness barrier passes the launcher engages via the existing tmux "agent" send-keys path exactly as scenario 04 (@scenario_hash:04236074a60ffcd7) pins, unchanged
    And the launcher starts no ephemeral fabro server and issues no "fabro run" on this default path, so the fabro engage replacement is confined to "--orchestrator fabro" (ADR-050 D1 tmux-default launch parity preserved)
