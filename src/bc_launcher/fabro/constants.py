"""Fabro def-bundle + orchestrator constants.

Extracted from the former bc_launcher/fabro.py (bead -7pa4 follow-up: fabro
package split). Re-exported via bc_launcher.fabro (the package __init__).
"""
from __future__ import annotations

from bc_launcher.constants import AGENT_CONTAINER_USER, CONTAINER_WORKSPACE




# ---------------------------------------------------------------------------
# Self-contained fabro loop def bundle (lead-h2bj — S2 def-bundle delivery)
# ---------------------------------------------------------------------------
#
# The launcher ships the bc-shop Implementer->Reviewer loop fabro def (ADR-051)
# as a set of packaged ASSET files under ``src/bc_launcher/assets/fabro-def/``
# and PLACES them into every launched container so the container carries a
# self-contained fabro def runnable FROM THE DEF ALONE — nothing is fetched at
# run time.  The def-root layout (``workflow.fabro`` / ``workflow.toml`` /
# ``project.toml`` / ``vaults/default/secrets.json`` / ``nodes/*.md``) is
# reproduced verbatim inside the container at ``/workspace/.fabro/`` (the
# project.toml comment pins that in a real ``.fabro/`` tree this file lives at
# ``.fabro/project.toml``).
#
# NATIVE-VAULT INVARIANT (ADR-049): the def's fabro vault
# (``vaults/default/secrets.json``) ships ``__PLACEHOLDER__``-only; real
# credentials ride the agent-vault surface (the shim + HTTPS_PROXY baked in
# S1), NEVER the fabro vault.  The asset file is placed verbatim, so no real
# secret is introduced by placement.
#
# Placement mechanism: the DockerDriver exposes only ``exec_run`` (docker exec)
# — there is no ``docker cp`` seam — so the bundle is placed by a single
# ``/bin/sh -c`` script that base64-decodes each file's bytes into its
# def-root-relative path.  base64 keeps the file bytes EXACTLY intact through
# the shell (no quoting/escaping/newline hazards regardless of file content),
# so the placed def is byte-identical to the shipped asset.
FABRO_DEF_CONTAINER_DIR = f"{CONTAINER_WORKSPACE}/.fabro"

FABRO_DEF_ASSET_SUBDIR = "assets/fabro-def"

# The def-root-relative paths the bundle ships (acceptance criterion 0).
# Enumerated explicitly so a dropped/renamed asset is a loud failure, not a
# silently-thinner bundle.  lead-odd9 / ADR-058 D2 adds the reactive-dispatcher
# def (dispatcher.fabro + its dispatcher.toml run config) alongside the
# UNCHANGED ADR-051 workflow.fabro child def, so the poured /workspace/.fabro/
# is runnable as `fabro run dispatcher.fabro` (the persistent engage) with the
# workflow.fabro child fanned out per work item at runtime.  lead-3zzu / ADR-058
# Amendment 2 adds dispatch_acp_agent.py -- the NON-LLM ACP script-agent the
# dispatcher's backend="acp" `dispatch` node drives (acp.command="python3
# dispatch_acp_agent.py") -- so the idempotent context-in/decisions-out dispatch
# is poured alongside the graph it belongs to.
FABRO_DEF_FILES: tuple[str, ...] = (
    "dispatcher.fabro",
    "dispatcher.toml",
    "dispatch_acp_agent.py",
    "workflow.fabro",
    "workflow.toml",
    "project.toml",
    "vaults/default/secrets.json",
    "nodes/bc-implementer.md",
    "nodes/bc-review.md",
    "nodes/bc-reviewer.md",
    "nodes/bc-router.md",
    "nodes/bc-sufficiency-check.md",
    "nodes/integrating-to-main.md",
    "nodes/subagent-driven-development.md",
    "nodes/test-driven-development.md",
    "nodes/using-git-worktrees.md",
    "nodes/work-done-gate.md",
    "nodes/writing-plans-bdd.md",
)



# ---------------------------------------------------------------------------
# Fabro orchestrator launch path — anthropic-oauth-shim + fabro provider wiring
# (lead-vwib — LAUNCHER WIRING ONLY; the shim itself is lead-so2h's owned
# artifact, a REAL stdlib ThreadingHTTPServer reverse proxy baked into bc-base
# v0.3.44 at /usr/local/bin/anthropic-oauth-shim)
# ---------------------------------------------------------------------------
#
# The DEFAULT launch path is the ADR-050 tmux/engage-tier path and is
# UNCHANGED by this wiring.  The FABRO orchestrator launch path is an
# ADDITIVE mode (``launch_path="fabro"`` / ``--fabro-path``) that, during
# bring-up, additionally:
#
#   (a) STARTS the baked so2h shim in-container as a background listener with
#       the shim's REAL serve args: ``anthropic-oauth-shim --host 127.0.0.1
#       --port 8788``.  Once listening, an in-container agent's Anthropic
#       traffic (its dummy ``x-api-key``) has a local endpoint; the shim
#       strips ``x-api-key``, adds ``Authorization: Bearer <dummy>`` +
#       ``anthropic-beta: oauth-2025-04-20``, and forwards via HTTPS_PROXY so
#       agent-vault injects the real credential on the wire.
#
#   (b) WRITES fabro's EFFECTIVE settings into the placed def at
#       ``/workspace/.fabro/settings.toml`` with ``[llm.providers.anthropic]``
#       ``base_url = "http://127.0.0.1:8788/v1"`` and ``adapter = "anthropic"``
#       (ADR-049 D2 — native Anthropic Messages format in both directions, NO
#       OpenAI<->Anthropic translation adapter).
#
# NATIVE-VAULT INVARIANT (ADR-049 D1): the fabro vault stays
# ``__PLACEHOLDER__``-only and NO real credential is written into the fabro
# settings or the shim's own configuration on this path.  The real Anthropic
# credential rides ONLY the agent-vault surface on the wire via the container
# HTTPS_PROXY (the live dummy-x-api-key -> shim -> HTTPS_PROXY -> agent-vault
# -> real-OAuth-200 round-trip is the lead's E2E, fabro-orchestration/02
# @scenario_hash:9c7b4e8280665239 — NOT this launch path's in-container core).
LAUNCH_PATH_TMUX = "tmux"

LAUNCH_PATH_FABRO = "fabro"


# The baked so2h shim binary + its REAL serve args (must match the shim's
# own argparse: --host / --port).  Binding on 127.0.0.1:8788 is the endpoint
# fabro's anthropic base_url points at.
ANTHROPIC_OAUTH_SHIM_BIN = "/usr/local/bin/anthropic-oauth-shim"

FABRO_SHIM_HOST = "127.0.0.1"

FABRO_SHIM_PORT = 8788


# The placed def's effective fabro settings file (settings.toml lives at the
# def root alongside workflow.fabro / project.toml).
FABRO_SETTINGS_CONTAINER_PATH = f"{FABRO_DEF_CONTAINER_DIR}/settings.toml"


# The placed def's workflow.toml (run/environment config for workflow.fabro).
# lead-ze4w BUG#2: the packaged asset carries byte-verbatim
# BC_NAME=fabro-throwaway / WORK_ID=fabro-spike-demo-3 in BOTH [run.inputs]
# (agent prompts) AND [run.environment.env] (the native script= sandbox
# overlay).  The native script= nodes (arm/armed/worktree/integ/wdg_r/emit_r)
# read $BC_NAME / $WORK_ID from the [run.environment.env] overlay, and `fabro
# run -I` overrides ONLY [run.inputs] (agent prompts), NOT the native command
# sandbox env — so the placed workflow.toml's overlay must be REWRITTEN to the
# launch's ACTUAL bc_name / work_id, or every native node runs against the
# bundle-default identity.  The launcher rewrites the placed workflow.toml the
# SAME way it (re)writes settings.toml: it generates the corrected file bytes
# on the host and base64-decode-writes them over the placed path.
FABRO_WORKFLOW_TOML_CONTAINER_PATH = f"{FABRO_DEF_CONTAINER_DIR}/workflow.toml"

# The placed def's dispatcher.toml — the reactive-persistent engage's actual
# entrypoint (`fabro run dispatcher.toml`, ADR-058).  lead-e5jx: the packaged
# asset carries byte-verbatim BC_NAME=fabro-throwaway in BOTH [run.inputs]
# (agent prompts) AND [run.environment.env] (the native script= sandbox
# overlay); the dispatcher's native watch/dispatch nodes read $BC_NAME from the
# [run.environment.env] overlay, and `fabro run -I` overrides ONLY [run.inputs]
# — so WITHOUT rewriting dispatcher.toml the reactive watcher runs
# `dispatch_acp_agent.py --bc fabro-throwaway` / `shop-msg watch --bc
# fabro-throwaway` (the bundle default) instead of the launch BC.  The launcher
# rewrites this file the SAME in-container read/rewrite/write-back way it
# rewrites workflow.toml.  (dispatcher.toml deliberately carries NO WORK_ID —
# the dispatcher discovers per-child work_ids at runtime; the shared rewrite
# simply finds no WORK_ID line to substitute.)
FABRO_DISPATCHER_TOML_CONTAINER_PATH = (
    f"{FABRO_DEF_CONTAINER_DIR}/dispatcher.toml"
)

# The bundle-default identity values the packaged workflow.toml ships (the
# values the rewrite must REPLACE).  Named so a test can assert the placed
# file no longer carries them.
FABRO_WORKFLOW_TOML_DEFAULT_BC_NAME = "fabro-throwaway"

FABRO_WORKFLOW_TOML_DEFAULT_WORK_ID = "fabro-spike-demo-3"


# The [llm.providers.anthropic] surface the launcher writes into fabro's
# effective settings on the fabro path.  base_url points the built-in
# anthropic provider at the local shim; adapter stays "anthropic" (native
# format, no translation — ADR-049 D2).  NO credential slot is written here:
# the credential rides agent-vault, never the fabro settings (ADR-049 D1).
FABRO_ANTHROPIC_BASE_URL = f"http://{FABRO_SHIM_HOST}:{FABRO_SHIM_PORT}/v1"

FABRO_ANTHROPIC_ADAPTER = "anthropic"


# ---------------------------------------------------------------------------
# OpenRouter no-shim agent-vault-brokered credential (lead-ifye3.2 behavior 3)
# ---------------------------------------------------------------------------
#
# On the `openrouter` active-provider override the launcher wires the OpenRouter
# credential the SAME no-shim way GITHUB_TOKEN rides agent-vault — NOT the
# Anthropic anthropic-oauth-shim header-reshaping way:
#
#   * OpenRouter is an OpenAI-compatible endpoint reached DIRECTLY (base_url =
#     https://openrouter.ai/api/v1, adapter="openai" — Authorization: Bearer
#     auth), so NO header-reshaping shim process is launched on this path
#     (contrast the anthropic path, which starts the anthropic-oauth-shim to
#     strip x-api-key and add the Bearer/oauth-beta headers).
#   * The node-side OPENROUTER_API_KEY is the literal AGENT_VAULT_PLACEHOLDER
#     token (__PLACEHOLDER__), exported into the engage env so the finite `fabro
#     run` children (provider=local, inheriting the engage env) carry it.  The
#     real key is NEVER in the container fs/env.
#   * The finite run's LLM request goes out with that placeholder Bearer token
#     and is forwarded through the container HTTPS_PROXY, where the agent-vault
#     broker's MITM proxy substitutes the REAL OpenRouter key onto the outbound
#     `Authorization: Bearer` header ON THE WIRE (mirrors GITHUB_TOKEN; ADR-026
#     / ADR-049 D1 — no real credential literal, the broker rides the wire).
FABRO_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

FABRO_OPENROUTER_ADAPTER = "openai"

# The container env var the fabro openrouter provider reads for its Bearer key.
OPENROUTER_API_KEY_ENV = "OPENROUTER_API_KEY"


# Fabro orchestrator ENGAGE step (lead-cadr — S4).  On the fabro launch path,
# AFTER the readiness barrier passes, the launcher REPLACES the tmux/claude
# engage tier (ADR-050 D3) with two execs:
#
#   1. Start an EPHEMERAL, in-container fabro server running provider=local in
#      the FOREGROUND with NO web UI, bound to a local 127.0.0.1 socket, via
#      the argv `fabro server start --foreground --no-web` — the loop runs
#      headless inside the ONE bc-base container; nothing is orchestrated
#      outside it.
#   2. Run the placed ADR-051 Implementer->Reviewer loop def against that
#      server as the engage: `fabro run workflow.fabro -I BC_NAME=<bc> -I
#      WORK_ID=<work_id>`, carrying BC_NAME + WORK_ID into the run via the
#      def's [run.environment.env].
#
# The engage tier is REPLACED, not added alongside: on the fabro path the
# launcher starts NO tmux `agent` send-keys session and NO `claude` engage
# (reproduces fabro-orchestration/01 @scenario_hash:1aeace4c593ab14f via the
# real bc-container launch path).  Container / credential-proxy / postgres DSN
# / shop-msg mailbox surfaces are UNCHANGED from the tmux path (ADR-050 D1/D2
# launch parity) — only the engage tier differs.
FABRO_BIN = "fabro"

# The placed def's workflow file (relative to the def dir the engage runs in).
# This is the UNCHANGED ADR-051 CHILD def: it is no longer the launcher's
# engage target (ADR-058 corrected that to dispatcher.fabro). The dispatcher's
# Haiku launch node spawns one detached `fabro run workflow.fabro` child per
# pending work item at RUNTIME.
FABRO_WORKFLOW_FILE = "workflow.fabro"

# lead-b3f0 / ADR-058 AMENDED (scenario A @scenario_hash:24d94274b9cbc2b0): the
# launcher's PERSISTENT REACTIVE engage target is the dispatcher's `.toml`
# ENTRYPOINT, `dispatcher.toml`, NOT the bare `dispatcher.fabro` graph def.
#
# ROOT CAUSE the .toml entrypoint fixes: `fabro run dispatcher.fabro` runs the
# `.fabro` graph def DIRECTLY, which BYPASSES the `[environments.local]`
# provider and DEFAULTS to a Docker-sandbox executor.  The bc-base container has
# no docker daemon, so that run fails in 0s ("Failed to connect to Docker
# daemon: /var/run/docker.sock") and the dispatcher EXITS before it ever polls
# the inbox — the BC comes up OFFLINE.  `fabro run dispatcher.toml` enters
# through the `.toml`, which applies `provider = local` (its `[environments.
# local]` block), so the sandbox comes up IN-PROCESS ("Sandbox: local ready")
# and every native node executes in-process with no docker sock connection.  The
# `.toml` binds `dispatcher.fabro` via its own `[workflow] graph=` and carries
# the `[run.environment.env] BC_NAME` overlay, so `-I BC_NAME=<bc>` still
# overrides the per-BC name at run time.
FABRO_DISPATCHER_FILE = "dispatcher.toml"



# lead-ze4w BUG#4: `fabro server start` reads a SERVER-level config at
# ~/.fabro/settings.toml.  The launcher previously wrote ONLY the workflow-level
# /workspace/.fabro/settings.toml, never the server-level file, so the server
# aborted `server.auth.methods: field is required` and `fabro run` could not
# reach it.  Before starting the server, `_fabro_engage` bootstraps the
# ephemeral server config:
#   1. `fabro install --non-interactive --skip-llm --overwrite-settings
#      --github-strategy token --github-username <dummy>` (with GH_TOKEN set)
#      writes a valid server-level ~/.fabro/settings.toml (auth.methods etc.).
#   2. register the built-in anthropic provider pointed at the shim base_url
#      (http://127.0.0.1:8788/v1) with a DUMMY ANTHROPIC_API_KEY in the server
#      env — the real credential rides agent-vault on the wire (ADR-049 D1),
#      NEVER this config, so the key is a fixed placeholder literal.
#
# lead-8q2x Defect A (INSTALL-FLAG DRIFT): on fabro 0.254.0 `fabro install
# --non-interactive --skip-llm --overwrite-settings` ABORTS with
# `x non-interactive install requires --github-strategy`.  The empirically-
# verified minimal recipe that resolves the flag chain (and still writes
# [server.auth] methods=["dev-token"] + session secret + dev token) adds
# `--github-strategy token --github-username <any>` with `GH_TOKEN=<any>` in
# the env.  fabro's github token is NOT exercised on this path — the
# anthropic-oauth-shim + agent-vault carry the real creds — so GH_TOKEN and
# --github-username are DUMMY placeholders (ADR-049 D1: no real cred literal).
FABRO_SERVER_INSTALL_GITHUB_USERNAME = "fabro-throwaway"

FABRO_SERVER_INSTALL_GH_TOKEN = "gh-dummy-agent-vault-rides-the-wire"

FABRO_SERVER_INSTALL_ARGV: tuple[str, ...] = (
    FABRO_BIN,
    "install",
    "--non-interactive",
    "--skip-llm",
    "--overwrite-settings",
    "--github-strategy",
    "token",
    "--github-username",
    FABRO_SERVER_INSTALL_GITHUB_USERNAME,
)

# The dummy ANTHROPIC_API_KEY placed in the ephemeral server's exec ENV (the
# `export ANTHROPIC_API_KEY=...` in `_fabro_engage_script`) so the registered
# anthropic provider is well-formed.  lead-sp2m: this dummy key is supplied
# ONLY via the server-env export — it is NEVER written into the settings TOML,
# because fabro 0.254.0 rejects `api_key` under [llm.providers.anthropic].
# ADR-049 D1: NO real credential — the real cred rides agent-vault on the wire.
# This is the only credential-shaped literal on this path and is deliberately a
# placeholder.
FABRO_SERVER_DUMMY_ANTHROPIC_KEY = "sk-ant-dummy-agent-vault-rides-the-wire"

# lead-8q2x Defect C (PROVIDER NOT REGISTERED AT SERVER): `--skip-llm` skips
# server-level provider registration, so even with the server up
# `fabro model test --model haiku` reports "not configured" — only the
# workflow-level /workspace/.fabro/settings.toml carries
# [llm.providers.anthropic], which the SERVER does NOT read for fabro
# model/run resolution.  So the engage bootstrap must register the anthropic
# provider AT THE SERVER by appending [llm.providers.anthropic]
# (adapter="anthropic" + base_url pointed at the shim, NO api_key — lead-sp2m;
# the DUMMY key rides the server-env ANTHROPIC_API_KEY export) to the
# server-level ~/.fabro/settings.toml AFTER `fabro install`.  The container
# user is `vscode` (HOME=/home/vscode),
# so the server-level config lives at /home/vscode/.fabro/settings.toml.
FABRO_SERVER_SETTINGS_CONTAINER_PATH = (
    f"/home/{AGENT_CONTAINER_USER}/.fabro/settings.toml"
)


# ---------------------------------------------------------------------------
# lead-1vbw / ADR-058 AMENDMENT-3 — the EXTERNAL agent-free message-driven
# watcher supervisor engage (replaces the infinite `fabro run dispatcher.toml`).
# ---------------------------------------------------------------------------
#
# ROOT CAUSE (lead-01jw, P0): the prior `--orchestrator fabro` engage ran ONE
# never-ending `fabro run dispatcher.toml` — a cyclic poll->dispatch->wait->poll
# loop whose per-5s-tick run-graph events accumulate UNBOUNDED in the fabro
# server heap (RSS grew 18->28GiB during PURE idle polling, OOM-bound).  The
# same infinite engage also failed the liveness heartbeat (lead-8hpz): a fabro
# BC was live-and-working yet reported offline because no shop-msg heartbeat was
# maintained.
#
# THE FIX: the engage becomes an EXTERNAL, agent-free, message-driven watcher
# supervisor whose ONLY always-resident process is `shop-msg watch --bc <name>`
# (a LISTEN/NOTIFY event source that emits a line only on a real message, never
# per poll tick, and that DOUBLES as the bc_presence liveness heartbeat — the
# lead-8hpz fix).  Each inbound inbox message fires ONE FINITE `fabro run
# workflow.fabro` child — the UNCHANGED ADR-051 child def, provider=local,
# WORK_ID/BC_NAME delivered via the per-child `[run.environment.env]` overlay.
# Idle => zero resident runs.
#
# PRODUCT-AUTHORITY DIRECTIVE (David, scenario 728871aca27b0d8f): the reference
# workaround (bin/bc-fabro-watch) started ONE EPHEMERAL server PER RUN and killed
# it on completion.  This engage instead starts EXACTLY ONE long-lived fabro
# server PER CONTAINER, bound to a container-scoped socket, persisting the whole
# container lifetime; every finite child fires against that ONE shared server
# (FABRO_SERVER targets the shared socket).  RATIONALE: a single persistent
# per-container server is OBSERVABLE — it exposes a scrapeable telemetry/metrics
# surface (scenario edc035fdde4062df) — whereas an ephemeral-per-run server
# vanishes before it can be measured.  CONSEQUENCE THE BC OWNS: memory safety no
# longer comes "for free" from per-run process death, so the watcher publishes a
# telemetry surface (resident memory + active/completed finite-run counts).

# The watcher's per-container state root (locks, the shared server socket +
# storage, the completed-run counter, the telemetry file).  Container-scoped so
# the ONE server + its telemetry persist the whole container lifetime.
FABRO_WATCH_STATE_DIR = f"{FABRO_DEF_CONTAINER_DIR}/.watch"

# The ONE long-lived per-container fabro server's container-scoped socket.  Every
# finite `fabro run workflow.fabro` child targets this via FABRO_SERVER, so the
# resident-server count is exactly 1 whether 0, 1, or many runs are in flight.
FABRO_WATCH_SERVER_SOCKET = f"{FABRO_WATCH_STATE_DIR}/fabro-server.sock"

# The ONE shared server's storage dir (its event heap lives here — the thing
# lead-01jw watches for monotonic growth).
FABRO_WATCH_SERVER_STORAGE = f"{FABRO_WATCH_STATE_DIR}/server-store"

# One lock subdir per live work_id — the atomic-mkdir in-flight dedup set, so
# exactly one finite child runs per work_id concurrently (scenario 9d737).
FABRO_WATCH_INFLIGHT_DIR = f"{FABRO_WATCH_STATE_DIR}/inflight"

# Monotonic completed-finite-run counter (bumped as each child reaches terminal)
# — one of the counts the telemetry surface publishes.
FABRO_WATCH_COMPLETED_FILE = f"{FABRO_WATCH_STATE_DIR}/completed.count"

# The scrapeable telemetry/metrics surface (scenario edc035fdde4062df): the ONE
# server's current resident memory + its active/completed finite-run counts,
# continuously observable across the container lifetime.
FABRO_WATCH_TELEMETRY_FILE = f"{FABRO_WATCH_STATE_DIR}/telemetry.json"

# The telemetry sampling cadence (seconds) — how often the supervisor refreshes
# the server RSS + run-count sample into the telemetry file.
FABRO_WATCH_TELEMETRY_INTERVAL_SECS = 15

# The MESSAGE-INDEPENDENT bc_presence heartbeat cadence (seconds) — lead-8hpz /
# scenario a5ce1af45ade7444 / ADR-050 D3 (ADDITIVE, extends structural pin
# e94a01b26ed6a4cc).  The supervisor's ONLY always-resident process is
# `shop-msg watch --bc <name>`, a LISTEN/NOTIFY event source that wakes ONLY on a
# real message and NEVER per poll tick — so it advances NO bc_presence heartbeat
# while the BC is idle-but-live (zero resident finite runs, no message in
# flight).  Without a message-independent cadence upsert an idle BC's
# last_seen_at ages past the bc-status staleness window (operator-confirmed
# ~2525s) and the BC reports OFFLINE + the container healthcheck reports
# UNHEALTHY though it is functionally healthy.  The supervisor therefore UPSERTs
# the heartbeat on THIS cadence, mirroring the telemetry sampler and bounded
# strictly below the bc-status ONLINE staleness window (shop_msg
# PRESENCE_ONLINE_MAX_SECONDS = 90), so an idle-but-live BC stays ONLINE.  The
# superseded "emit a heartbeat each 5s poll" fix-direction is SUPERSEDED: this is
# a bounded cadence upsert INDEPENDENT of message arrival, NOT a poll tick.
FABRO_WATCH_HEARTBEAT_INTERVAL_SECS = 30

# The bound (seconds) on each heartbeat's `shop-msg watch`: a bounded watch fires
# its FIRST action — a bc_presence UPSERT keyed on the SAME canonical presence
# name bc-status queries — then is killed, so the cadence loop can repeat.  Kept
# small so the effective heartbeat period
# (FABRO_WATCH_HEARTBEAT_INTERVAL_SECS + this) stays well below the staleness
# window.
FABRO_WATCH_HEARTBEAT_BOUND_SECS = 5
