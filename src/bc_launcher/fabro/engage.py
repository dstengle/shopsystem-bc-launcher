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
    """The argv the launcher's watcher fires for each FINITE child run
    (lead-1vbw / ADR-058 AMENDMENT-3, superseding the retired persistent
    ``fabro run dispatcher.toml`` engage).

    Each inbound inbox message fires ONE FINITE ``fabro run`` child that runs the
    UNCHANGED ADR-051 ``workflow.fabro`` graph against the ONE long-lived
    per-container fabro server.  The per-child WORK_ID/BC_NAME are delivered via
    the materialized child config's ``[run.environment.env]`` overlay (NOT via
    ``-I``, which does not reach the native ``script=`` nodes — the byte-identical
    reference recipe), so this argv carries no ``-I`` and runs a per-child config
    file.  ``bc_name`` is accepted for signature compatibility with the historical
    import sites; the concrete finite-child argv is issued inside the watcher
    supervisor (``_fabro_engage_script``).  Returned as a list so callers can
    describe the finite-child run.
    """
    return [FABRO_BIN, "run", FABRO_WORKFLOW_FILE]



def _fabro_engage_script(bc_name: str) -> str:
    """Build the ``/bin/sh -c`` script that drives the fabro ENGAGE step — the
    EXTERNAL agent-free message-driven watcher supervisor (lead-1vbw / ADR-058
    AMENDMENT-3, superseding the retired infinite ``fabro run dispatcher.toml``
    engage; keeps lead-cadr + lead-ze4w BUG#4 + lead-esy4 Defect D + lead-8q2x
    bootstrap invariants intact).

    ROOT CAUSE the watcher fixes (lead-01jw, P0): the prior engage ran ONE
    never-ending ``fabro run dispatcher.toml`` — a cyclic
    poll->dispatch->wait->poll loop whose per-tick run-graph events accumulated
    UNBOUNDED in the fabro server heap (RSS 18->28GiB during PURE idle polling,
    OOM-bound), and it maintained NO shop-msg heartbeat (lead-8hpz:
    live-but-offline).

    THE ENGAGE IS NOW A WATCHER SUPERVISOR.  The recorded ``command[2]`` script,
    read top to bottom, is:

      1. UNCHANGED server-config bootstrap (the clone-path fix the watcher STILL
         needs): ``cd`` into the def dir FIRST (so ``fabro run`` children resolve
         the poured ``workflow.fabro``, not ``/workspace/workflow.fabro``); export
         SSL_CERT_FILE + the DUMMY ANTHROPIC_API_KEY + the shim ANTHROPIC_BASE_URL
         BEFORE ``fabro install`` (esy4 Defect D); ``fabro install`` writes a
         VALID server-level ``~/.fabro/settings.toml`` ([server.auth] methods +
         64-hex SESSION_SECRET + ``fabro_dev_``+64hex FABRO_DEV_TOKEN) so the
         server does not die ``server.auth.methods: field is required``; append
         the ``[llm.providers.anthropic]`` provider block (adapter + shim
         base_url, NO api_key — lead-sp2m) at the server.
      2. Start EXACTLY ONE long-lived per-container fabro server, bound to a
         container-scoped socket + storage dir, FOREGROUND, NO web UI
         (``fabro server start --foreground --no-web``), backgrounded inside its
         OWN brace group so ONLY the server detaches and the ``cd``/install stay
         synchronous (lead-8q2x Defect B).  This is the ONE shared server for the
         whole container lifetime (product-authority directive, scenario
         728871aca27b0d8f) — NOT one ephemeral server per run.
      3. Publish a scrapeable telemetry surface (scenario edc035fdde4062df): the
         supervisor samples the ONE server's resident memory (``VmRSS``) + its
         active/completed finite-run counts into a telemetry file, refreshed on a
         cadence, so memory safety — which no longer comes "for free" from per-run
         process death — is CONTINUOUSLY observable.
      4. STARTUP DRAIN + per-message dispatch (scenario 9d737bcd0f4473e9): the
         authoritative pending set is ``shop-msg pending inbox --bc <name>``;
         each pending/incoming work_id fires ONE FINITE child, guarded by an
         atomic ``mkdir`` in-flight lock (dedup — exactly one child per work_id
         concurrently).  Each finite child runs the UNCHANGED ADR-051
         ``workflow.fabro`` graph via a byte-identical materialized child config
         (provider=local, WORK_ID/BC_NAME via ``[run.environment.env]`` — the
         f38ab guarantee) against the ONE shared server (``FABRO_SERVER`` targets
         the shared socket).  A finite child failure is logged and SWALLOWED as
         NON-FATAL: the lock is released and the supervisor keeps serving
         (scenario 7a4f7eed52594107).  The dispatch path is AGENT-FREE — no
         claude / LLM anywhere; steady-state supervision spends ZERO model tokens.
      5. The ONLY always-resident process is ``shop-msg watch --bc <name>`` — a
         LISTEN/NOTIFY event source that emits a line only on a real message
         (never per poll tick) AND doubles as the bc_presence liveness HEARTBEAT
         (scenario e94a01b26ed6a4cc, the lead-8hpz fix).  Idle => ZERO resident
         fabro runs (scenario 47da82f60bbd47a9).

    The engage exec is issued DETACHED at the docker level (``exec -d``, lead-lwk4
    R7) by ``_fabro_engage``, so ``launch()`` returns after issuing this long-lived
    supervisor rather than blocking on it.
    """
    import shlex

    install_argv = " ".join(
        shlex.quote(tok) for tok in _fabro_server_install_argv()
    )
    server_argv = " ".join(
        shlex.quote(tok) for tok in _fabro_server_start_argv()
    )
    def_dir = shlex.quote(FABRO_DEF_CONTAINER_DIR)
    base_url = shlex.quote(FABRO_ANTHROPIC_BASE_URL)
    dummy_key = shlex.quote(FABRO_SERVER_DUMMY_ANTHROPIC_KEY)
    gh_token = shlex.quote(FABRO_SERVER_INSTALL_GH_TOKEN)
    server_settings = shlex.quote(FABRO_SERVER_SETTINGS_CONTAINER_PATH)
    server_log = shlex.quote(f"{FABRO_DEF_CONTAINER_DIR}/fabro-server.log")
    run_log = shlex.quote(f"{FABRO_DEF_CONTAINER_DIR}/fabro-watch.log")

    # Watcher state (container-scoped: the ONE server + its telemetry persist the
    # whole container lifetime).  Bare (unquoted) forms are used where they are
    # embedded in shell word positions with no special chars; quoted forms guard
    # redirections / arguments.
    state_dir = FABRO_WATCH_STATE_DIR
    sock = FABRO_WATCH_SERVER_SOCKET
    store = FABRO_WATCH_SERVER_STORAGE
    inflight = FABRO_WATCH_INFLIGHT_DIR
    completed = FABRO_WATCH_COMPLETED_FILE
    telemetry = FABRO_WATCH_TELEMETRY_FILE
    interval = FABRO_WATCH_TELEMETRY_INTERVAL_SECS
    q_state = shlex.quote(state_dir)
    q_sock = shlex.quote(sock)
    q_store = shlex.quote(store)
    q_inflight = shlex.quote(inflight)
    q_completed = shlex.quote(completed)
    q_telemetry = shlex.quote(telemetry)
    q_bc = shlex.quote(bc_name)
    workflow = FABRO_WORKFLOW_FILE

    # (Defect C) the [llm.providers.anthropic] block appended to the SERVER-level
    # ~/.fabro/settings.toml so the provider is registered AT THE SERVER
    # (adapter + shim base_url, NO api_key — lead-sp2m; the DUMMY key rides the
    # ANTHROPIC_API_KEY server-env export, never the settings TOML — ADR-049 D1).
    provider_block = (
        "\\n[llm.providers.anthropic]\\n"
        f'adapter = "{FABRO_ANTHROPIC_ADAPTER}"\\n'
        f'base_url = "{FABRO_ANTHROPIC_BASE_URL}"\\n'
    )
    provider_register = (
        f"printf '%b' {shlex.quote(provider_block)} >> {server_settings}"
    )

    # --- (1)/(2) bootstrap + start the ONE per-container server -------------
    # SYNCHRONOUS `&&` chain (cd first, exports before install — esy4/ze4w),
    # backgrounding ONLY the foreground server inside its own brace group
    # (lead-8q2x Defect B); the whole AND-list is NOT terminated by a bare `&`.
    bootstrap = (
        f"cd {def_dir} && "
        f'export {SSL_CERT_FILE_ENV}={shlex.quote(AGENT_VAULT_CONTAINER_CA_PATH)} && '
        f"export ANTHROPIC_API_KEY={dummy_key} && "
        f"export ANTHROPIC_BASE_URL={base_url} && "
        f"GH_TOKEN={gh_token} {install_argv} && "
        f"{provider_register} && "
        f"mkdir -p {q_state} {q_inflight} {q_store} && "
        f": > {q_completed} && "
        f"{{ nohup {server_argv} --bind {q_sock} --storage-dir {q_store} "
        f">{server_log} 2>&1 & }} && "
        f"FABRO_SERVER_PID=$!"
    )

    # --- (3)/(4)/(5) the external agent-free watcher supervisor -------------
    # Plain shell (functions + loops) after the bootstrap AND-chain.  Every finite
    # child targets the ONE shared server via the exported FABRO_SERVER; run_finite
    # never starts or kills a server (negative control for scenario 728871).
    watcher = f"""
BC_NAME={q_bc}
FABRO_SERVER={sock}
export FABRO_SERVER
# Bounded readiness wait for the ONE shared server socket.
_waited=0
while [ ! -S {sock} ]; do
  kill -0 "$FABRO_SERVER_PID" 2>/dev/null || break
  _waited=$((_waited + 1)); [ "$_waited" -ge 150 ] && break
  sleep 0.2
done
# Telemetry (scenario edc035): sample the ONE server's resident memory (VmRSS)
# + active (in-flight lock count) and completed finite-run counts into a
# scrapeable telemetry file, so bounded-vs-monotonic memory is observable.
sample_telemetry() {{
  _rss="$(awk '/VmRSS/ {{ print $2 " " $3 }}' /proc/"$FABRO_SERVER_PID"/status 2>/dev/null)"
  _active="$(ls -1 {q_inflight} 2>/dev/null | wc -l | tr -d ' ')"
  _completed="$(cat {q_completed} 2>/dev/null || echo 0)"
  printf '{{"server_pid":"%s","resident_memory":"%s","active_runs":%s,"completed_runs":%s}}\\n' \\
    "$FABRO_SERVER_PID" "$_rss" "${{_active:-0}}" "${{_completed:-0}}" > {q_telemetry}
}}
sample_telemetry
( while kill -0 "$FABRO_SERVER_PID" 2>/dev/null; do sample_telemetry; sleep {interval}; done ) &
# Materialize the finite child config — BYTE-IDENTICAL to the reference's
# materialize_child: UNCHANGED ADR-051 workflow.fabro graph, provider=local,
# WORK_ID/BC_NAME delivered to the native script= nodes via [run.environment.env]
# (the f38ab guarantee — `-I WORK_ID` does NOT reach them).
materialize_child() {{
  _mc_wid="$1"; _mc_path="$2"
  cat > "$_mc_path" <<EOF
[workflow]
graph = "{workflow}"
[run.inputs]
BC_NAME = "$BC_NAME"
WORK_ID = "$_mc_wid"
[run.environment.env]
BC_NAME = "$BC_NAME"
WORK_ID = "$_mc_wid"
[run.environment]
id = "local"
[environments.local]
provider = "local"
[run.pull_request]
enabled = false
EOF
}}
# run_finite <work_id>: fire ONE finite `fabro run` child that runs the UNCHANGED
# workflow.fabro graph against the ONE shared server (FABRO_SERVER, exported).
# NON-FATAL: any failure is logged + swallowed, the completed counter is bumped,
# and the in-flight lock is ALWAYS released so the supervisor keeps serving.
run_finite() {{
  _rf_wid="$1"
  _rf_sw="$(printf '%s' "$_rf_wid" | tr -c 'A-Za-z0-9._-' '_')"
  _rf_child={def_dir}/child-"$_rf_sw".toml
  materialize_child "$_rf_wid" "$_rf_child" || {{ echo "materialize $_rf_wid failed (non-fatal)" >>{run_log} 2>&1; }}
  # DIRECT the finite child at the ONE already-running shared server via
  # --server "$FABRO_SERVER" (lead-oqaw / scenario 9f785e78ed55da4b).  Exporting
  # FABRO_SERVER into the ambient shell is NOT enough: without an explicit server
  # target on the `fabro run` command the child AUTOSTARTS its own server and
  # fabro refuses `Server already running (pid <n>)` — the child exits 1 with no
  # work_done and the dispatch stays stuck pending.  Passing --server attaches
  # this child to the shared server, so the resident fabro-server count stays
  # EXACTLY 1 no matter how many finite children are in flight.
  fabro run --server "$FABRO_SERVER" "child-$_rf_sw.toml" --auto-approve >>{run_log} 2>&1
  _rf_rc=$?
  echo "$(( $(cat {q_completed} 2>/dev/null || echo 0) + 1 ))" > {q_completed} 2>/dev/null || true
  rm -f "$_rf_child" 2>/dev/null || true
  rm -rf {q_inflight}/"$_rf_sw" 2>/dev/null || true
  sample_telemetry
  if [ "$_rf_rc" -ne 0 ]; then
    echo "child $_rf_wid exited $_rf_rc (non-fatal; watcher continues)" >>{run_log} 2>&1
  fi
  return "$_rf_rc"
}}
# dispatch <work_id>: atomic-mkdir in-flight dedup — spawn exactly once per live
# work_id; SKIP if a child is already running for it.  The detached worker
# isolates a child failure from the always-resident watch reader (non-fatal).
dispatch() {{
  _d_wid="$1"; [ -z "$_d_wid" ] && return 0
  _d_sw="$(printf '%s' "$_d_wid" | tr -c 'A-Za-z0-9._-' '_')"
  if mkdir {q_inflight}/"$_d_sw" 2>/dev/null; then
    run_finite "$_d_wid" &
  else
    echo "skip $_d_wid: child already in flight (dedup)" >>{run_log} 2>&1
  fi
}}
# drain: authoritative pending set = `shop-msg pending inbox --bc <name>`
# (idempotent — a work_id whose child already responded work_done is absent).
drain() {{
  _dr_out="$(shop-msg pending inbox --bc "$BC_NAME" 2>>{run_log})" || return 0
  [ -z "$_dr_out" ] && return 0
  printf '%s\\n' "$_dr_out" | while IFS= read -r _dr_line; do
    _dr_wid="$(printf '%s' "$_dr_line" | awk '{{ print $1 }}')"
    [ -n "$_dr_wid" ] && dispatch "$_dr_wid"
  done
}}
# Startup drain (scenario 9d737): fire a finite child for each pre-existing
# pending work id so nothing that arrived between sessions is missed.
drain
# Supervise: the ONLY always-resident process is `shop-msg watch --bc <name>`
# (LISTEN/NOTIFY wake + bc_presence heartbeat — scenario e94a01 / lead-8hpz).
# Each real message is a WAKE; dispatch is dedup-guarded.  On stream end, sweep
# the gap via drain, then restart watch.  AGENT-FREE: no model-backed agent
# anywhere in this dispatch path — steady-state supervision spends ZERO tokens.
while true; do
  shop-msg watch --bc "$BC_NAME" 2>>{run_log} | while IFS=' ' read -r _w_wid _w_mtype; do
    [ "$_w_wid" = "READY" ] && continue
    [ -z "$_w_wid" ] && continue
    dispatch "$_w_wid"
  done
  drain
  sleep 2
done
"""
    return bootstrap + watcher


def _fabro_exec_env() -> dict[str, str]:
    """The extra exec env the launcher pins on the fabro shim + engage execs.

    lead-ze4w BUG#3: these execs run in a non-login `/bin/sh -c` that never
    sources /etc/profile.d/agent-vault-ca.sh, so SSL_CERT_FILE (the python /
    urllib CA-trust var) is empty and the shim's upstream HTTPS through
    HTTPS_PROXY fails CERTIFICATE_VERIFY_FAILED.  Set SSL_CERT_FILE explicitly
    to the SAME materialized broker CA path the clone path points git at via
    GIT_SSL_CAINFO, so the shim + engage trust the agent-vault MITM CA without
    a login shell.
    """
    return {SSL_CERT_FILE_ENV: AGENT_VAULT_CONTAINER_CA_PATH}
