"""Fabro server/run/engage argv + engage shell script.

Extracted from the former bc_launcher/fabro.py (bead -7pa4 follow-up: fabro
package split). Re-exported via bc_launcher.fabro (the package __init__).
"""
from __future__ import annotations

from bc_launcher.constants import AGENT_VAULT_CONTAINER_CA_PATH, SSL_CERT_FILE_ENV
from bc_launcher.fabro.constants import *  # noqa: F401,F403  (sibling constants)




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
