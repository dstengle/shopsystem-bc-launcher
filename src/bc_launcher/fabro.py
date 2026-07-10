"""
Self-contained fabro loop def bundle + fabro orchestrator launch-path wiring.

Extracted verbatim from ``controller`` (bead shopsystem_bc_launcher-7pa4, Phase 1
of the controller.py decomposition).  This module owns the packaged fabro-def
asset bundle (placement scripts), the anthropic-oauth-shim + fabro provider
wiring, and the settings/workflow TOML (re)write scripts.

It is a leaf that depends only on the standard library and ``bc_launcher.constants``
(never on ``controller``), so ``controller`` can import from it without a cycle.
Every public name here is re-exported by ``controller`` for import-path
compatibility (``from bc_launcher.controller import _load_fabro_def_files`` etc.).

The fabro-def assets are resolved relative to THIS module via ``__file__``; since
this module ships in the same package directory as ``controller`` (and the
``assets/`` tree), the resolution is unchanged by the move.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from bc_launcher.constants import (
    AGENT_CONTAINER_USER,
    AGENT_VAULT_CONTAINER_CA_PATH,
    CONTAINER_WORKSPACE,
    SSL_CERT_FILE_ENV,
)


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


def _fabro_def_asset_root() -> Path:
    """Absolute path to the packaged fabro-def asset directory.

    Resolves relative to THIS module so the bundle is found whether the
    launcher runs from a source checkout or an installed wheel (the assets are
    packaged as package data under ``bc_launcher/assets/``).
    """
    return Path(__file__).resolve().parent / FABRO_DEF_ASSET_SUBDIR


def _load_fabro_def_files() -> dict[str, bytes]:
    """Read the 15 packaged def-bundle asset files as raw bytes.

    Returns a mapping of def-root-relative path -> file bytes.  Raises
    ``FileNotFoundError`` if any enumerated asset is missing, so a broken
    package surfaces loudly rather than placing a thinner bundle.
    """
    root = _fabro_def_asset_root()
    out: dict[str, bytes] = {}
    for rel in FABRO_DEF_FILES:
        src = root / rel
        out[rel] = src.read_bytes()
    return out


def _fabro_def_install_script(
    files: dict[str, bytes],
    dest_dir: str = FABRO_DEF_CONTAINER_DIR,
) -> str:
    """Build a ``/bin/sh -c`` script that places the def bundle into a container.

    lead-h2bj.  Each file's bytes are base64-encoded on the HOST and decoded on
    the CONTAINER into ``<dest_dir>/<relpath>``, so the placed def is
    byte-identical to the shipped asset regardless of file content (no shell
    quoting/escaping/newline hazards).  The script creates each file's parent
    directory first so the ``nodes/`` and ``vaults/default/`` subtrees are
    reproduced exactly.
    """
    import base64
    import shlex

    lines = ["set -e", f"mkdir -p {shlex.quote(dest_dir)}"]
    for rel in FABRO_DEF_FILES:
        data = files[rel]
        b64 = base64.b64encode(data).decode("ascii")
        target = f"{dest_dir}/{rel}"
        parent = os.path.dirname(target)
        q_target = shlex.quote(target)
        q_parent = shlex.quote(parent)
        lines.append(f"mkdir -p {q_parent}")
        # base64 -d is POSIX-portable on the bc-base image (coreutils).
        lines.append(f"printf %s {shlex.quote(b64)} | base64 -d > {q_target}")
    return "\n".join(lines) + "\n"


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


def _fabro_server_start_argv() -> list[str]:
    """The argv the launcher uses to START the ephemeral in-container fabro
    server on the fabro engage path (lead-cadr).

    provider=local, FOREGROUND, NO web UI, bound to a local 127.0.0.1 socket:
    the loop runs headless inside the one bc-base container.  Returned as a
    list so the test can assert the launcher issues exactly this argv.
    """
    return [FABRO_BIN, "server", "start", "--foreground", "--no-web"]


def _fabro_server_install_argv() -> list[str]:
    """The argv the launcher uses to BOOTSTRAP the ephemeral server config
    before `fabro server start` (lead-ze4w BUG#4).

    `fabro install --non-interactive --skip-llm --overwrite-settings` writes a
    valid server-level ~/.fabro/settings.toml (carrying the required
    server.auth.methods surface), so `fabro server start` does not abort
    `server.auth.methods: field is required`.
    """
    return list(FABRO_SERVER_INSTALL_ARGV)


def _fabro_run_argv(bc_name: str) -> list[str]:
    """The argv the launcher uses to RUN the PERSISTENT REACTIVE DISPATCHER def
    against the ephemeral fabro server as the ENGAGE step (lead-odd9 / ADR-058
    D1, correcting lead-cadr's one-shot).

    `fabro run dispatcher.fabro -I BC_NAME=<bc_name>` — ONE persistent run that
    OWNS the container's lifecycle and discovers work_ids at RUNTIME.  It
    carries ONLY the constant BC_NAME into the run via the def's
    [run.environment.env]; it supplies NO `-I WORK_ID` and requires NO launch-
    time work id (ADR-058 D1/D6).  The prior one-shot `fabro run workflow.fabro
    -I BC_NAME -I WORK_ID` engage (retired) ran the child def directly on a
    launch-time work id; under ADR-058 the dispatcher's Haiku launch node spawns
    one detached `fabro run workflow.fabro` child per pending work item at
    runtime instead.  Returned as a list so the test can assert the launcher
    issues exactly this argv with the scenario's BC_NAME.
    """
    return [
        FABRO_BIN,
        "run",
        FABRO_DISPATCHER_FILE,
        "-I",
        f"BC_NAME={bc_name}",
    ]


def _fabro_engage_script(bc_name: str) -> str:
    """Build the ``/bin/sh -c`` script that drives the fabro ENGAGE step in the
    placed def dir (lead-cadr + lead-ze4w BUG#4 + lead-odd9 / ADR-058).

    lead-odd9 / ADR-058 D1: the ENGAGE run is now the PERSISTENT REACTIVE
    ``fabro run dispatcher.fabro -I BC_NAME=<bc>`` (no WORK_ID), not the retired
    one-shot ``fabro run workflow.fabro -I BC_NAME -I WORK_ID``.  Everything else
    on this path — the ~/.fabro server-config bootstrap (BUG#4), the
    env-before-install ordering (esy4 Defect D), the cwd=/workspace/.fabro run
    (Defect B) — is UNCHANGED and is exactly the ADR-058 bundled clone-path fix
    (@scenario_hash:cacccc52ba0b0766).

    lead-ze4w BUG#4: BEFORE `fabro server start`, bootstrap the SERVER-level
    fabro config so the server does not abort
    ``server.auth.methods: field is required``:
      1. `fabro install --non-interactive --skip-llm --overwrite-settings
         --github-strategy token --github-username <dummy>` (GH_TOKEN set)
         writes a valid server-level ~/.fabro/settings.toml.
      2. register the anthropic provider pointed at the shim base_url
         (http://127.0.0.1:8788/v1) with a DUMMY ANTHROPIC_API_KEY in the
         server env (ADR-049 D1: no real credential — the real cred rides
         agent-vault on the wire).

    lead-8q2x — the ze4w Fix#4 bootstrap was broken 3 ways at runtime; all
    three are corrected here:

      A) INSTALL-FLAG DRIFT: on fabro 0.254.0 the bare install aborts
         ``x non-interactive install requires --github-strategy``.
         `_fabro_server_install_argv` now emits
         `--github-strategy token --github-username <dummy>` and the install
         runs with a DUMMY `GH_TOKEN` inline (github token not exercised on
         this path).
      B) `&` CWD-SCOPING: previously the trailing `&` backgrounded the WHOLE
         `cd && install && ... && nohup server` AND-list, so the `cd` ran
         inside the backgrounded subshell and the PARENT shell cwd stayed
         /workspace (the image WORKDIR) — `fabro run workflow.fabro` then
         resolved /workspace/workflow.fabro -> "x workflow not found".  Now
         the `cd {def_dir}` + install + provider registration run
         SYNCHRONOUSLY in the SAME shell as `fabro run`, and ONLY the
         foreground server is backgrounded (`nohup ... &` on its own line).
         `fabro run` therefore executes with cwd=/workspace/.fabro and
         resolves /workspace/.fabro/workflow.fabro.
      C) PROVIDER NOT REGISTERED AT SERVER: `--skip-llm` skips server-level
         provider registration, so `fabro model test` reported "not
         configured" even with the server up — the SERVER does not read the
         workflow-level settings for model resolution.  The bootstrap now
         APPENDS a schema-valid `[llm.providers.anthropic]` block
         (adapter="anthropic" + shim base_url, NO api_key — lead-sp2m) to the
         server-level ~/.fabro/settings.toml AFTER install, so the provider is
         registered AT THE SERVER.  The DUMMY key rides the ANTHROPIC_API_KEY
         server-env export, never the settings file.

    Then start the ephemeral fabro server in the FOREGROUND with no web UI and
    run the loop def against it.  The server's foreground serve loop blocks, so
    ONLY the server is backgrounded; the ``fabro run`` engage runs
    synchronously in the same shell (cwd=/workspace/.fabro) so
    ``workflow.fabro`` resolves and the server picks up the effective settings
    the launcher wrote alongside it.
    """
    import shlex

    install_argv = " ".join(
        shlex.quote(tok) for tok in _fabro_server_install_argv()
    )
    server_argv = " ".join(
        shlex.quote(tok) for tok in _fabro_server_start_argv()
    )
    run_argv = " ".join(
        shlex.quote(tok) for tok in _fabro_run_argv(bc_name)
    )
    def_dir = shlex.quote(FABRO_DEF_CONTAINER_DIR)
    server_log = shlex.quote(f"{FABRO_DEF_CONTAINER_DIR}/fabro-server.log")
    # lead-i0wi F3: the backgrounded (detached) `fabro run` engage's stdout/stderr
    # is captured to a run log so detaching does not discard the run's output.
    run_log = shlex.quote(f"{FABRO_DEF_CONTAINER_DIR}/fabro-run.log")
    base_url = shlex.quote(FABRO_ANTHROPIC_BASE_URL)
    dummy_key = shlex.quote(FABRO_SERVER_DUMMY_ANTHROPIC_KEY)
    gh_token = shlex.quote(FABRO_SERVER_INSTALL_GH_TOKEN)
    server_settings = shlex.quote(FABRO_SERVER_SETTINGS_CONTAINER_PATH)
    # (Defect C) The [llm.providers.anthropic] block appended to the
    # server-level ~/.fabro/settings.toml so the provider is registered AT THE
    # SERVER pointed at the shim.  Written with printf via a here-doc-free
    # append so it needs no base64 helper on the container.
    #
    # lead-sp2m (Fix C correction): the block writes ONLY schema-valid fields
    # — `adapter = "anthropic"` + `base_url = "<shim>/v1"`.  It writes NO
    # `api_key` line: fabro 0.254.0 REJECTS `api_key` under
    # [llm.providers.anthropic] ("unknown field `api_key`") so `fabro validate`
    # exits 1 and the server can't start.  The DUMMY key is supplied instead
    # via the ANTHROPIC_API_KEY ENVIRONMENT VARIABLE in the server's exec env
    # (the export below), exactly the Slice-0/Slice-3 spike recipe.  ADR-049
    # D1: no real credential anywhere in fabro's settings/vault — the real
    # cred rides agent-vault on the wire.
    provider_block = (
        "\\n[llm.providers.anthropic]\\n"
        f'adapter = "{FABRO_ANTHROPIC_ADAPTER}"\\n'
        f'base_url = "{FABRO_ANTHROPIC_BASE_URL}"\\n'
    )
    provider_register = (
        f"printf '%b' {shlex.quote(provider_block)} >> {server_settings}"
    )
    # (Defect A/B/C + lead-esy4 Defect D) Bootstrap the server-level config
    # SYNCHRONOUSLY, then background ONLY the foreground server, then run the
    # def in the SAME shell (cwd=/workspace/.fabro):
    #   * `cd {def_dir}` first, SYNCHRONOUS — so `fabro run` resolves
    #     /workspace/.fabro/workflow.fabro (NOT /workspace/workflow.fabro);
    #   * export the DUMMY ANTHROPIC_API_KEY + agent-vault CA (SSL_CERT_FILE) +
    #     shim base_url (ANTHROPIC_BASE_URL) BEFORE `fabro install` — see the
    #     lead-esy4 Defect D note below;
    #   * `GH_TOKEN=<dummy> fabro install ... --github-strategy token
    #     --github-username <dummy>` writes a valid ~/.fabro/settings.toml and
    #     no longer aborts on the flag chain;
    #   * append [llm.providers.anthropic] (adapter="anthropic" + shim
    #     base_url, NO api_key — schema-valid; lead-sp2m) to the SERVER-level
    #     settings so the provider is registered at the server;
    #   * background ONLY `nohup {server} ... &` (its OWN line) so the run can
    #     engage against it while the parent shell stays in {def_dir}.
    #
    # lead-esy4 Defect D (the FINAL fabro engage bug): `fabro install
    # --non-interactive ...` STARTS the fabro serving daemon.  Previously the
    # `export ANTHROPIC_API_KEY=<dummy>` (+ SSL_CERT_FILE + ANTHROPIC_BASE_URL)
    # exports came AFTER `fabro install`, so the daemon `fabro install` spawned
    # had NO LLM key in its env; the subsequent `fabro server start` could not
    # bind ("× Server already running", a no-op) and `fabro run` then targeted
    # the keyless install-daemon whose preflight FAILED "No LLM providers
    # configured. Set ANTHROPIC_API_KEY or OPENAI_API_KEY".  FIX: move the three
    # exports to BEFORE `fabro install`, so the install-spawned daemon inherits
    # ANTHROPIC_API_KEY (+ the shim base_url + the broker CA) and `fabro run`
    # preflight passes.  ADR-049 D1 invariant intact: the key is a DUMMY
    # placeholder; the real credential rides agent-vault on the wire, and the
    # fabro settings/vault stay __PLACEHOLDER__ (no api_key in the TOML).
    #
    # The now-redundant `fabro server start` (its serving daemon is already the
    # install-daemon that inherited the key) is KEPT but backgrounded inside the
    # brace group so its "× Server already running" no-op returns 0 immediately
    # and does not break the `&&` chain before `fabro run`.  It is retained
    # because scenario 77 (@scenario_hash:68e14cdcd8b7c145) structurally pins
    # the `fabro server start --foreground --no-web` argv in the rendered engage
    # script (both `_fabro_server_start_argv()` and `assert server_argv in
    # call.command[2]`); dropping it would RED that signed-off lead pin, which
    # lead-esy4 acceptance (iv) requires green.  The FUNCTIONAL fix is the
    # env-before-install reordering; the retained server-start is harmless.
    #
    # NOTE (Defect B): the server is backgrounded via a brace group
    # ``{ nohup ... & }`` so ONLY the server subprocess is detached; the
    # surrounding `&&` chain (cd, exports, install, provider-register) runs
    # SYNCHRONOUSLY in the CURRENT shell, so the cwd set by `cd {def_dir}`
    # persists to `fabro run` on the last line.  (If the whole `cd && ... &&
    # nohup server` list were terminated by a bare trailing `&`, the entire
    # list — including the `cd` — would run in a backgrounded subshell and the
    # parent shell cwd would stay at the image WORKDIR; that was the bug.)
    # lead-lwk4 R7 (LAUNCH ACTUALLY RETURNS AFTER ENGAGE — DOCKER-LEVEL DETACH):
    # `_fabro_engage` issues this script through `driver.exec_run`.  The v0.3.49
    # lead-i0wi F3 fix backgrounded the `fabro run` engage INSIDE the script
    # (`{ nohup ... & }`), but that was INEFFECTIVE: a synchronous `docker exec`
    # captures the exec's stdout/stderr via pipes and reads them to EOF, and the
    # backgrounded `{ nohup server & }` / `{ nohup run & }` children INHERIT
    # those pipes, so `subprocess.run` never sees EOF and `docker exec` (hence
    # `launch()`) BLOCKS for the lifetime of the foreground fabro server —
    # nohup-inside-the-script does NOT detach the child stdio from the exec.
    #
    # FIX: detach at the DOCKER level.  `_fabro_engage` now issues this SAME
    # script via `driver.exec_run(..., detach=True)` (docker `exec -d`), so the
    # docker daemon runs the engage in the background and `docker exec -d`
    # RETURNS IMMEDIATELY without attaching to (or reading) the exec's
    # stdout/stderr — the child stdio never rides the launcher's pipes, so the
    # blocking `subprocess.run` returns at once and `launch()` RETURNS after the
    # engage is issued.  This mirrors the tmux path (a detached `tmux
    # new-session -d` that daemonizes and returns), but detaches at the EXEC
    # level so the ENGAGE SCRIPT itself is UNCHANGED — the `command[2]` payload
    # the launcher records is byte-for-byte the same `cd {def_dir} && ... &&
    # fabro run ...` chain, so every structural pin that reads command[2] as a
    # prefix/substring (scn 77 @scenario_hash:68e14cdcd8b7c145, the esy4
    # Defect-D ordering, the 8q2x Defect B/C shape) stays green verbatim.  The
    # fabro server + run keep running headless in the container after `exec -d`
    # returns.
    #
    # The script keeps the foreground `fabro server start` backgrounded (its OWN
    # brace group, log-redirected) so ONLY the server is detached WITHIN the
    # script, and `fabro run` runs synchronously in the engage shell AFTER it —
    # exactly the 8q2x Defect-B shape.  The engage exec being `exec -d`, the
    # launcher does not block on that synchronous `fabro run` either.
    #
    # INVARIANTS PRESERVED:
    #   * esy4 Defect D: the three exports still PRECEDE `fabro install`, and the
    #     `cd {def_dir}` + install + provider-register still run SYNCHRONOUSLY in
    #     the engage shell BEFORE the run is engaged, so the run inherits
    #     cwd=/workspace/.fabro (workflow.fabro resolves) and the LLM key.
    #   * esy4 (ii) + scn 77 (@scenario_hash:68e14cdcd8b7c145): the
    #     `fabro server start --foreground --no-web` argv stays RETAINED inside its
    #     `{ nohup ... & }` brace group, and the `fabro run workflow.fabro -I
    #     BC_NAME=... -I WORK_ID=...` argv is still ISSUED — detaching at the
    #     DOCKER (`exec -d`) level changes HOW the exec is issued, not WHAT.
    #   Teeth (lead-lwk4 R7): issue the engage exec WITHOUT detach (synchronous
    #   `exec_run(detach=False)`) -> the R7 detach test REDs.
    return (
        f"cd {def_dir} && "
        f'export {SSL_CERT_FILE_ENV}={shlex.quote(AGENT_VAULT_CONTAINER_CA_PATH)} && '
        f"export ANTHROPIC_API_KEY={dummy_key} && "
        f"export ANTHROPIC_BASE_URL={base_url} && "
        f"GH_TOKEN={gh_token} {install_argv} && "
        f"{provider_register} && "
        f"{{ nohup {server_argv} >{server_log} 2>&1 & }} && "
        f"{run_argv} >{run_log} 2>&1\n"
    )


def _fabro_shim_start_argv() -> list[str]:
    """The argv the launcher uses to START the baked so2h shim in serve mode.

    These are the shim's REAL serve args (``--host`` / ``--port`` per the
    committed shim's argparse) targeting the fixed 127.0.0.1:8788 endpoint
    that fabro's anthropic base_url points at.  Returned as a list so the
    test can assert the launcher starts the shim at that mode + host + port.
    """
    return [
        ANTHROPIC_OAUTH_SHIM_BIN,
        "--host",
        FABRO_SHIM_HOST,
        "--port",
        str(FABRO_SHIM_PORT),
    ]


def _fabro_shim_start_script() -> str:
    """Build the ``/bin/sh -c`` script that starts the baked so2h shim as a
    BACKGROUND listener on 127.0.0.1:8788.

    The shim's ``serve_forever`` blocks, so it is backgrounded (``&``) and
    disowned via ``nohup`` so the exec returns and the listener survives.
    The argv is the shim's REAL serve args (``--host 127.0.0.1 --port 8788``).
    """
    import shlex

    argv = " ".join(shlex.quote(tok) for tok in _fabro_shim_start_argv())
    # nohup + background so the exec returns while the shim keeps listening;
    # its own stderr log line ("[shim] listening on ...") goes to a logfile
    # under the def dir so a launch never blocks on the serving loop.
    log = shlex.quote(f"{FABRO_DEF_CONTAINER_DIR}/anthropic-oauth-shim.log")
    # lead-ze4w BUG#3: the shim runs in a NON-LOGIN /bin/sh, so it never
    # sources /etc/profile.d/agent-vault-ca.sh -> SSL_CERT_FILE is empty and
    # the shim's urllib does not trust the agent-vault MITM CA (upstream HTTPS
    # via HTTPS_PROXY fails CERTIFICATE_VERIFY_FAILED).  Export SSL_CERT_FILE
    # explicitly to the materialized broker CA path (parallel to the clone
    # path's GIT_SSL_CAINFO export) so the shim trusts the MITM CA.
    ssl_export = (
        f"export {SSL_CERT_FILE_ENV}="
        f"{shlex.quote(AGENT_VAULT_CONTAINER_CA_PATH)}"
    )
    return f"{ssl_export}\nnohup {argv} >{log} 2>&1 &\n"


def _fabro_settings_toml() -> str:
    """The effective fabro settings TOML the launcher writes on the fabro path.

    Carries ``[llm.providers.anthropic]`` with ``base_url`` pointed at the
    local shim and ``adapter = "anthropic"`` (native format, no translation).
    Writes NO credential slot — the real Anthropic credential rides
    agent-vault on the wire, never fabro's settings (ADR-049 D1/D2).
    """
    return (
        "# settings.toml -- EFFECTIVE fabro settings written by the "
        "shopsystem-bc-launcher\n"
        "# fabro orchestrator launch path (lead-vwib).  Points fabro's "
        "built-in\n"
        "# anthropic provider at the in-container anthropic-oauth-shim "
        "(lead-so2h)\n"
        f"# listening on {FABRO_SHIM_HOST}:{FABRO_SHIM_PORT}.  The adapter "
        'stays "anthropic"\n'
        "# so the shim speaks native Anthropic Messages format in both "
        "directions;\n"
        "# NO OpenAI<->Anthropic translation adapter is introduced "
        "(ADR-049 D2).\n"
        "#\n"
        "# ADR-049 D1: NO real credential is written here.  The real "
        "Anthropic\n"
        "# credential rides ONLY the agent-vault surface on the wire via the\n"
        "# container HTTPS_PROXY; fabro's native vault stays "
        '"__PLACEHOLDER__"-only.\n'
        "\n"
        "[llm.providers.anthropic]\n"
        f'base_url = "{FABRO_ANTHROPIC_BASE_URL}"\n'
        f'adapter = "{FABRO_ANTHROPIC_ADAPTER}"\n'
    )


def _fabro_settings_install_script(
    dest_path: str = FABRO_SETTINGS_CONTAINER_PATH,
) -> str:
    """Build a ``/bin/sh -c`` script that writes the effective fabro settings
    into the placed def at ``dest_path``.

    The TOML bytes are base64-encoded on the HOST and decoded on the
    CONTAINER (same byte-safe channel the def-bundle placement uses), so the
    written settings are byte-identical to ``_fabro_settings_toml()``
    regardless of content.
    """
    import base64
    import shlex

    data = _fabro_settings_toml().encode("utf-8")
    b64 = base64.b64encode(data).decode("ascii")
    q_target = shlex.quote(dest_path)
    q_parent = shlex.quote(os.path.dirname(dest_path))
    return (
        "set -e\n"
        f"mkdir -p {q_parent}\n"
        f"printf %s {shlex.quote(b64)} | base64 -d > {q_target}\n"
    )


def _fabro_workflow_toml_rewrite(source: str, bc_name: str, work_id: str) -> str:
    """Rewrite the packaged workflow.toml's BC_NAME / WORK_ID to the launch's
    ACTUAL values (lead-ze4w BUG#2).

    The packaged asset ships BC_NAME / WORK_ID in TWO tables:
      * ``[run.inputs]``          — the agent-prompt inputs (`fabro run -I`
                                    overrides these, but only for prompts);
      * ``[run.environment.env]`` — the env overlay that reaches the native
                                    ``script=`` sandbox as real shell env vars
                                    ($BC_NAME / $WORK_ID), which `-I` does NOT
                                    override.

    Both carry the bundle defaults (``fabro-throwaway`` /
    ``fabro-spike-demo-3``).  This rewrites EVERY ``BC_NAME = "..."`` and
    ``WORK_ID = "..."`` assignment (in either table) to the launch's actual
    ``bc_name`` / ``work_id``, so the native nodes run against the real
    identity rather than the bundle default.  Modeled on the settings.toml
    (re)write: the corrected bytes are produced on the host and written over
    the placed file.
    """
    def _sub(line: str) -> str:
        # Match a top-of-line TOML key assignment `KEY = "value"` (optional
        # trailing comment preserved), for BC_NAME / WORK_ID only.
        m = re.match(
            r'^(?P<key>BC_NAME|WORK_ID)(?P<sp>\s*=\s*)"[^"]*"(?P<rest>.*)$',
            line,
        )
        if not m:
            return line
        value = bc_name if m.group("key") == "BC_NAME" else work_id
        # Escape any embedded double-quote / backslash so the emitted TOML
        # string stays well-formed regardless of the identity value.
        safe = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'{m.group("key")}{m.group("sp")}"{safe}"{m.group("rest")}'

    return "\n".join(_sub(line) for line in source.split("\n"))


def _fabro_workflow_toml_install_script(
    bc_name: str,
    work_id: str,
    dest_path: str = FABRO_WORKFLOW_TOML_CONTAINER_PATH,
) -> str:
    """Build a ``/bin/sh -c`` script that (re)writes the placed workflow.toml
    with the launch's ACTUAL BC_NAME / WORK_ID (lead-ze4w BUG#2).

    Reads the packaged workflow.toml asset, rewrites the BC_NAME / WORK_ID
    assignments in ``[run.inputs]`` and ``[run.environment.env]`` to the
    launch's values, then base64-decode-writes the corrected bytes over the
    placed ``workflow.toml`` — the SAME byte-safe channel + overwrite
    mechanism the launcher uses to (re)write settings.toml.
    """
    import base64
    import shlex

    asset = (_fabro_def_asset_root() / "workflow.toml").read_text(
        encoding="utf-8"
    )
    rewritten = _fabro_workflow_toml_rewrite(asset, bc_name, work_id)
    b64 = base64.b64encode(rewritten.encode("utf-8")).decode("ascii")
    q_target = shlex.quote(dest_path)
    q_parent = shlex.quote(os.path.dirname(dest_path))
    return (
        "set -e\n"
        f"mkdir -p {q_parent}\n"
        f"printf %s {shlex.quote(b64)} | base64 -d > {q_target}\n"
    )
