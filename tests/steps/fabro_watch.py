"""Step definitions: the EXTERNAL agent-free message-driven watcher supervisor
engage (lead-1vbw / ADR-058 AMENDMENT-3).

Covers the reshaped orchestrator watcher-tier re-pin (6352660f8b7ce05c), the
reshaped clone-path server-config bootstrap re-pin (402241f3f31cecd9), and the 7
watcher scenarios (47da82f60bbd47a9, 728871aca27b0d8f, edc035fdde4062df,
4d2411e2050345bc, e94a01b26ed6a4cc, 7a4f7eed52594107, 9d737bcd0f4473e9).

FIDELITY: every assertion binds to the REAL launcher's ACTUAL recorded
`--orchestrator fabro` engage script over the FakeDockerDriver (drive via
_odd9_drive_fabro_launch, read via _cadr_fabro_engage_call), never a model.
Dockerless: the watcher engage is validated STRUCTURALLY over the recorded
`/bin/sh -c` engage script — the >=50-run live-memory scenario (4d2411) is
xfail-bound because it requires a live server + fabro-side reclamation (lead-01jw).
"""
from __future__ import annotations

import re

from pytest_bdd import given, when, then, parsers  # noqa: F401

from tests.conftest import (  # noqa: F401
    _ODD9_SERVER_SETTINGS_PATH,
    _ODD9_PROJECT_SETTINGS_PATH,
    _ODD9_DEF_DIR,
    _cadr_server_start_argv,
)
from tests.support.container import (  # noqa: F401
    _ODD9_BC,
    _cadr_exec_calls,
    _cadr_fabro_engage_call,
    _cadr_fabro_run_calls,
    _cadr_fabro_server_calls,
    _cadr_tmux_agent_send_keys,
    _cadr_claude_engage_send_keys,
    _odd9_drive_fabro_launch,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ensure_launched(ctx, fake_driver, controller, tmp_path):
    """Idempotently drive the REAL launcher on the --orchestrator fabro path so
    the recorded watcher engage exists.  Scenarios whose only Given is "the
    container is running the external watcher engage" drive the launch here;
    scenarios that already ran an explicit launch Given are a no-op."""
    if "cadr_result" not in ctx:
        _odd9_drive_fabro_launch(_ODD9_BC, ctx, fake_driver, controller,
                                 tmp_path, work_id=None)


def _script(ctx):
    call = _cadr_fabro_engage_call(ctx)
    assert call is not None, (
        "the fabro-path launcher did not emit a watcher engage exec; "
        f"exec_calls: {[c.command[:3] for c in _cadr_exec_calls(ctx)]!r}"
    )
    return call.command[2]


# ===========================================================================
# Reshaped orchestrator watcher-tier re-pin — 6352660f8b7ce05c
# ===========================================================================

@then(
    "AFTER the readiness barrier passes the launcher engages the EXTERNAL "
    "agent-free message-driven watcher supervisor "
    "(bc_container_fabro_engage_external_watcher, "
    "@scenario_hash:47da82f60bbd47a9) as the fabro-path engage tier, running NO "
    'long-lived "fabro run dispatcher.toml" reactive dispatcher on this path')
def repin6352_watcher_tier(ctx):
    script = _script(ctx)
    assert 'shop-msg watch --bc "$BC_NAME"' in script, (
        f"the fabro-path engage must be the external `shop-msg watch` watcher "
        f"supervisor; script:\n{script}"
    )
    assert "fabro run dispatcher.toml" not in script, (
        "the fabro path must run NO long-lived `fabro run dispatcher.toml` "
        f"reactive dispatcher; script:\n{script}"
    )


@then(
    'the launcher starts EXACTLY ONE long-lived per-container fabro server '
    'running "provider=local" in the foreground with no web UI, issuing the '
    'argv "fabro server start --foreground --no-web", against which the watcher '
    'fires its finite "fabro run workflow.fabro" children '
    "(bc_container_fabro_engage_external_watcher, "
    "@scenario_hash:728871aca27b0d8f), so the loop runs headless inside the one "
    "bc-base container and nothing is orchestrated outside it")
def repin6352_one_server(ctx):
    script = _script(ctx)
    argv = " ".join(_cadr_server_start_argv())
    assert argv == "fabro server start --foreground --no-web", (
        f"the pinned server-start argv must be {argv!r}"
    )
    assert script.count("fabro server start") == 1, (
        "EXACTLY ONE per-container fabro server must be started (not one "
        f"ephemeral server per run); count={script.count('fabro server start')}"
    )
    assert argv in script, f"the engage must issue {argv!r}; script:\n{script}"
    assert 'graph = "workflow.fabro"' in script, (
        "each finite child must run the UNCHANGED workflow.fabro graph; "
        f"script:\n{script}"
    )


@then(
    'no tmux "agent" send-keys session and no "claude" engage is started on '
    "this path, the engage tier being REPLACED by the fabro watcher run-graph "
    "entry rather than added alongside it (ADR-050 D3)")
def repin6352_no_tmux_no_claude(ctx):
    assert _cadr_tmux_agent_send_keys(ctx) == [], (
        "fabro path must start NO tmux 'agent' send-keys session"
    )
    assert _cadr_claude_engage_send_keys(ctx) == [], (
        "fabro path must start NO 'claude' engage"
    )
    assert _cadr_fabro_server_calls(ctx), "fabro server start must be present"
    script = _script(ctx)
    assert 'shop-msg watch --bc "$BC_NAME"' in script, (
        "the engage tier must be REPLACED by the fabro watcher run-graph entry"
    )


# ===========================================================================
# Reshaped clone-path server-config bootstrap re-pin — 402241f3f31cecd9
# ===========================================================================

@given(
    'the container "bc-shopsystem-messaging" has cloned the repo and '
    'shop-templates has POURED "/workspace/.fabro/" including the UNCHANGED '
    'ADR-051 "workflow.fabro" child def the watcher\'s finite children run')
def repin4022_cloned_poured(ctx, fake_driver, controller, tmp_path):
    _ensure_launched(ctx, fake_driver, controller, tmp_path)
    assert fake_driver.is_running("bc-shopsystem-messaging"), (
        "the clone-path fabro launch must leave the container running"
    )
    ctx["container_name"] = "bc-shopsystem-messaging"


@when(
    "the launcher's recorded fabro watcher engage steps — the server config it "
    'provisions, the "fabro server start" argv, and the working directory of '
    'the watcher\'s "fabro run" children — are inspected structurally, without '
    "a live docker daemon, a running fabro server, or a reachable agent-vault")
def repin4022_inspect(ctx):
    ctx["repin4022_inspected"] = True


@then(
    "the launcher runs the external watcher engage — whose finite "
    '"fabro run workflow.fabro" children fire against the one per-container '
    'server — with the working directory set to the project dir '
    '"/workspace/.fabro", NOT "/workspace", so fabro resolves the poured '
    '"workflow.fabro" rather than failing "workflow not found: '
    '/workspace/workflow.fabro"')
def repin4022_cwd(ctx):
    script = _script(ctx)
    assert script.lstrip().startswith("cd /workspace/.fabro &&"), (
        f"the engage must `cd /workspace/.fabro` FIRST; script:\n{script}"
    )
    assert 'graph = "workflow.fabro"' in script, (
        "the finite children must run the poured workflow.fabro graph"
    )
    assert "/workspace/workflow.fabro" not in script, (
        "the engage must NOT resolve /workspace/workflow.fabro (the WORKDIR-root "
        f"path the clone-path bug produced); script:\n{script}"
    )


@then(
    'as the observable result a fresh clone-path "--orchestrator fabro" launch '
    "REACHES the fabro watcher engage successfully — the in-container fabro "
    'server comes up and the watcher\'s "fabro run" children resolve the poured '
    "def — instead of crashing at server auth bootstrap or def resolution as "
    "the un-provisioned clone path currently does (ADR-058 bundled fix, "
    "lead-l4iw)")
def repin4022_reaches(ctx):
    assert ctx["cadr_result"].exit_code == 0, (
        "the fresh clone-path fabro launch must REACH the watcher engage (exit 0)"
    )
    assert _cadr_fabro_server_calls(ctx), "fabro server start must be present"
    assert _cadr_fabro_run_calls(ctx), "the finite `fabro run` children must be present"


# ===========================================================================
# Watcher scenario 47da82f60bbd47a9 — external watcher; no dispatcher; zero idle
# ===========================================================================

@given(
    'the container "bc-shopsystem-messaging" is running with the self-contained '
    'fabro def set POURED by shop-templates into "/workspace/.fabro/", '
    'including the UNCHANGED ADR-051 "workflow.fabro" finite child def')
def watch47_container(ctx, fake_driver, controller, tmp_path):
    _ensure_launched(ctx, fake_driver, controller, tmp_path)
    assert fake_driver.is_running("bc-shopsystem-messaging")
    ctx["container_name"] = "bc-shopsystem-messaging"


@when(
    'the launcher\'s recorded "--orchestrator fabro" engage command is '
    "inspected structurally, without a live docker daemon, a running fabro "
    "server, or a reachable agent-vault")
def watch47_inspect(ctx):
    ctx["watch_inspected"] = True


@then(
    "the engage starts the external message-driven watcher supervisor whose "
    'ONLY always-resident process is "shop-msg watch --bc shopsystem-messaging" '
    "(which holds NO run-graph and emits a line only on a real inbox message, "
    "never per poll tick), and which fires exactly ONE finite "
    '"fabro run workflow.fabro" child per inbound inbox message')
def watch47_always_resident(ctx):
    script = _script(ctx)
    assert f"BC_NAME='{_ODD9_BC}'" in script or f"BC_NAME={_ODD9_BC}" in script, (
        f"the watcher must bind BC_NAME to {_ODD9_BC}; script:\n{script}"
    )
    assert 'shop-msg watch --bc "$BC_NAME"' in script, (
        "the always-resident process must be `shop-msg watch --bc <name>`; "
        f"script:\n{script}"
    )
    # The finite child per message: the UNCHANGED workflow.fabro graph, fired via
    # the dedup-guarded dispatch on each watch wake.
    assert 'graph = "workflow.fabro"' in script and "dispatch " in script, (
        "each wake must fire ONE finite workflow.fabro child via dispatch; "
        f"script:\n{script}"
    )


@then(
    'the engage does NOT run a long-lived "fabro run dispatcher.toml" nor '
    '"fabro run dispatcher.fabro": there is NO infinite cyclic '
    "poll->dispatch->wait->poll run resident in a fabro server, so the per-tick "
    "run-graph event accumulation that grew the server heap 18->28GiB during "
    "pure idle polling (lead-01jw) cannot occur")
def watch47_no_dispatcher(ctx):
    script = _script(ctx)
    assert "fabro run dispatcher.toml" not in script, (
        f"the engage must run NO `fabro run dispatcher.toml`; script:\n{script}"
    )
    assert "fabro run dispatcher.fabro" not in script, (
        f"the engage must run NO `fabro run dispatcher.fabro`; script:\n{script}"
    )


@then(
    "with no inbound message in flight the engage holds ZERO resident fabro "
    "runs, so steady-state idle retains no per-run event state at all")
def watch47_zero_idle(ctx):
    script = _script(ctx)
    # Structurally: a fabro run child is fired ONLY inside run_finite, which is
    # invoked ONLY by dispatch on a real wake/pending work id — never as a
    # persistent resident run.  So with no message in flight, zero fabro runs.
    assert "run_finite" in script and "dispatch " in script, (
        "finite runs must be fired per-message via dispatch->run_finite, so idle "
        f"holds ZERO resident runs; script:\n{script}"
    )
    # No always-on `fabro run` outside the per-message worker.
    assert "fabro run dispatcher" not in script


# ===========================================================================
# Watcher scenario 728871aca27b0d8f — EXACTLY ONE per-container server
# ===========================================================================

@given(
    'the container "bc-shopsystem-messaging" is running the external '
    "message-driven watcher engage (scenario 1)")
def watch72_container(ctx, fake_driver, controller, tmp_path):
    _ensure_launched(ctx, fake_driver, controller, tmp_path)
    assert fake_driver.is_running("bc-shopsystem-messaging")


@when(
    "the watcher's fabro-server lifecycle and its per-message finite-run "
    "invocation are inspected structurally, without a live docker daemon, a "
    "running fabro server, or a reachable agent-vault")
def watch72_inspect(ctx):
    ctx["watch_inspected"] = True


@then(
    "the engage starts EXACTLY ONE fabro server once, bound to a single "
    "container-scoped socket, and that one server persists for the whole "
    "container lifetime rather than being started and killed per run")
def watch72_one_server(ctx):
    from tests.conftest import _ODD9_DEF_DIR  # def dir root
    script = _script(ctx)
    assert script.count("fabro server start") == 1, (
        "EXACTLY ONE `fabro server start`; "
        f"count={script.count('fabro server start')}; script:\n{script}"
    )
    # Bound to a single container-scoped socket (--bind <sock>).
    assert "--bind" in script and f"{_ODD9_DEF_DIR}/.watch" in script, (
        "the one server must bind a container-scoped socket under the watch "
        f"state dir; script:\n{script}"
    )


@then(
    'each inbound message fires a finite "fabro run workflow.fabro" child '
    "against that ONE shared server (the child's FABRO_SERVER targets the "
    "shared container socket), so the count of resident fabro servers is "
    "exactly 1 whether 0, 1, or many finite runs are in flight")
def watch72_children_share_server(ctx):
    script = _script(ctx)
    assert "FABRO_SERVER=" in script and "export FABRO_SERVER" in script, (
        "each finite child must target the ONE shared server via the exported "
        f"FABRO_SERVER; script:\n{script}"
    )
    assert script.count("fabro server start") == 1, (
        "resident fabro servers must be exactly 1 regardless of in-flight runs"
    )


@then(
    "as the negative control, the engage does NOT start one ephemeral fabro "
    "server per run and kill it on completion (the prior reference-workaround "
    "shape in bin/bc-fabro-watch), because a per-run server vanishes before it "
    "can be scraped whereas the single persistent server is observable "
    "(scenario 3)")
def watch72_negative_control(ctx):
    script = _script(ctx)
    # The per-message worker fires only `fabro run`; it never starts or kills a
    # server (no `fabro server start` inside run_finite).
    rf_start = script.find("run_finite()")
    rf_end = script.find("dispatch()", rf_start)
    assert rf_start != -1 and rf_end != -1, "run_finite/dispatch must be defined"
    worker = script[rf_start:rf_end]
    assert "fabro server start" not in worker, (
        "run_finite must NOT start an ephemeral per-run server; script:\n{script}"
    )
    assert "kill" not in worker or "server" not in worker.lower(), (
        "run_finite must NOT kill a per-run server"
    )


# ===========================================================================
# Watcher scenario edc035fdde4062df — telemetry surface
# ===========================================================================

@given(
    'the container "bc-shopsystem-messaging" is running the external watcher '
    "engage with exactly one long-lived per-container fabro server (scenario 2)")
def watched_one_server(ctx, fake_driver, controller, tmp_path):
    _ensure_launched(ctx, fake_driver, controller, tmp_path)
    assert fake_driver.is_running("bc-shopsystem-messaging")


@when("the per-container server's observability surface is inspected while the "
      "container runs")
def watch_ed_inspect(ctx):
    ctx["watch_inspected"] = True


@then(
    "the single per-container fabro server exposes a telemetry/metrics surface "
    "that can be scraped for at minimum the server's current resident memory "
    "and its active and completed finite-run counts over time")
def watch_ed_surface(ctx):
    from tests.conftest import _ODD9_DEF_DIR
    script = _script(ctx)
    assert f"{_ODD9_DEF_DIR}/.watch" in script and "telemetry" in script, (
        "the watcher must publish a scrapeable telemetry surface; "
        f"script:\n{script}"
    )
    assert "VmRSS" in script, "telemetry must sample the server's resident memory"
    assert "active_runs" in script and "completed_runs" in script, (
        "telemetry must publish active and completed finite-run counts"
    )


@then(
    "because the server is long-lived and singular this telemetry is "
    "CONTINUOUSLY observable across the container lifetime, which is the "
    "deciding reason the engage uses ONE per-container server rather than an "
    "ephemeral-per-run server that vanishes before it can be measured")
def watch_ed_continuous(ctx):
    script = _script(ctx)
    # A sampler loop refreshes the telemetry for the server's lifetime.
    assert "sample_telemetry" in script and "while kill -0" in script, (
        "telemetry must be refreshed on a cadence for the server's lifetime; "
        f"script:\n{script}"
    )
    assert script.count("fabro server start") == 1


@then(
    "the telemetry is sufficient to detect whether the server's retained memory "
    "returns toward baseline or grows monotonically across successive finite "
    "runs, serving as the measurement instrument scenario 4 asserts against")
def watch_ed_instrument(ctx):
    script = _script(ctx)
    assert "VmRSS" in script and "completed_runs" in script, (
        "the telemetry must pair server RSS with the completed-run count so "
        "bounded-vs-monotonic memory is detectable across runs"
    )


# ===========================================================================
# Watcher scenario 4d2411e2050345bc — >=50-run bounded memory (XFAIL-bound)
# ===========================================================================

@given(
    'the container "bc-shopsystem-messaging" is running the external watcher '
    "engage with exactly one long-lived per-container fabro server (scenario 2) "
    "exposing run and memory telemetry (scenario 3)")
def watch4d_container(ctx, fake_driver, controller, tmp_path):
    _ensure_launched(ctx, fake_driver, controller, tmp_path)


@given("a baseline of the per-container server's resident memory is recorded "
       "while no finite run is in flight")
def watch4d_baseline(ctx):
    ctx["watch4d_baseline"] = True


@when(
    'at least 50 sequential inbound messages each fire and drive a finite '
    '"fabro run workflow.fabro" child to its terminal (done or halt) against '
    "the shared server, one after another")
def watch4d_50_runs(ctx):
    # Dockerless: a live server + 50 real finite runs cannot be exercised here;
    # this leg's bounded-memory outcome also depends on FABRO-SIDE reclamation
    # (escalated in lead-01jw).  The scenario is xfail-bound at the binding.
    ctx["watch4d_ran"] = True
    raise AssertionError(
        "requires a live fabro server + 50 finite runs; the bounded outcome "
        "depends on fabro-side reclamation escalated in lead-01jw (dockerless "
        "env cannot exercise it) — xfail-bound"
    )


@then(
    "after each finite run reaches its terminal the shared server RELEASES that "
    "run's event state, so the server's retained resident memory returns toward "
    "the recorded baseline rather than retaining the completed run's events")
def watch4d_releases(ctx):
    assert ctx.get("watch4d_ran")


@then(
    "after all the runs the server's peak retained resident memory is within a "
    "bounded delta of baseline and does NOT increase monotonically with the run "
    "count, so the shared-server memory is BOUNDED across many runs even though "
    'it no longer comes "for free" from per-run process death')
def watch4d_bounded(ctx):
    assert ctx.get("watch4d_ran")


@then(
    "if instead the telemetry shows retained memory climbing monotonically with "
    "the run count, that is the fabro-side reclamation defect escalated in "
    "lead-01jw which the telemetry (scenario 3) makes visible as the escalation "
    "signal, and the required behavior this scenario pins remains the bounded "
    "one")
def watch4d_escalation(ctx):
    assert ctx.get("watch4d_ran")


# ===========================================================================
# Watcher scenario e94a01b26ed6a4cc — bc_presence heartbeat via shop-msg watch
# ===========================================================================

@given('the container "bc-shopsystem-messaging" is running the external watcher '
       "engage (scenario 1)")
def watch_running_engage(ctx, fake_driver, controller, tmp_path):
    _ensure_launched(ctx, fake_driver, controller, tmp_path)
    assert fake_driver.is_running("bc-shopsystem-messaging")


@when(
    'the always-resident "shop-msg watch --bc shopsystem-messaging" process '
    "runs for longer than the bc-status staleness window")
def watch_e9_when(ctx):
    ctx["watch_inspected"] = True


@then(
    "that same process UPSERTs the bc_presence (bc_name, last_seen_at) "
    'heartbeat on a cadence inside the staleness window, so "shop-msg '
    'bc-status" classifies "shopsystem-messaging" as ONLINE and the container '
    "healthcheck reports healthy")
def watch_e9_heartbeat(ctx):
    script = _script(ctx)
    # `shop-msg watch` is the always-resident process AND the sole heartbeat
    # source (it UPSERTs bc_presence from the same LISTEN connection).
    assert 'shop-msg watch --bc "$BC_NAME"' in script, (
        "the heartbeat is maintained by the always-resident `shop-msg watch`; "
        f"script:\n{script}"
    )


@then(
    'as the negative control, the superseded infinite "fabro run '
    'dispatcher.toml" engage maintained NO shop-msg heartbeat, so a fabro BC '
    "was live-and-working yet reported offline with a stale heartbeat and an "
    "unhealthy healthcheck (lead-8hpz), which this watcher-maintained heartbeat "
    "fixes")
def watch_e9_negative(ctx):
    script = _script(ctx)
    assert "fabro run dispatcher.toml" not in script, (
        "the superseded infinite `fabro run dispatcher.toml` engage (no "
        f"heartbeat) must be gone; script:\n{script}"
    )
    assert 'shop-msg watch --bc "$BC_NAME"' in script


# ===========================================================================
# Watcher scenario 7a4f7eed52594107 — agent-free + non-fatal child
# ===========================================================================

@when(
    "the watcher's dispatch path is inspected structurally and a finite child "
    "run for one message terminates with a NON-zero exit")
def watch_7a_when(ctx):
    ctx["watch_inspected"] = True


@then(
    "NO claude, LLM, or model-backed agent appears anywhere in the watcher's "
    'dispatch path: the always-resident process is "shop-msg watch" and each '
    'dispatch fires a finite native "fabro run workflow.fabro" child, so '
    "steady-state supervision spends ZERO model tokens")
def watch_7a_agent_free(ctx):
    script = _script(ctx)
    # Agent-free = NO `claude` CLI / model-backed agent is EXECUTED in the
    # dispatch path.  lead-ifye3.2 behavior 4 supplies provider-keyed model IDs
    # as `-I MODEL_*=<id>` fabro-run inputs; the Anthropic-row IDs are
    # `claude-haiku-4-5` — DATA (a hyphenated model slug), not an agent
    # invocation — so match a BARE `claude` agent token, not the model slug.
    assert not re.search(r"(?<![\w./-])claude(?![\w-])", script), (
        "the watcher dispatch path must be agent-free (no `claude` CLI agent "
        f"executed); script:\n{script}"
    )
    assert 'shop-msg watch --bc "$BC_NAME"' in script
    assert 'graph = "workflow.fabro"' in script


@then(
    "a finite child that exits non-zero is logged and swallowed as NON-FATAL: "
    'it does not terminate the watcher, the always-resident "shop-msg watch" '
    "keeps running, that child's in-flight lock is released, and subsequent "
    "inbound messages continue to be dispatched")
def watch_7a_non_fatal(ctx):
    script = _script(ctx)
    # The child runs in a detached, lock-releasing worker (`run_finite ... &`),
    # so its non-zero exit never propagates to the always-resident watch reader.
    assert "run_finite" in script, "the child must run in an isolated run_finite worker"
    assert "non-fatal" in script.lower(), (
        "a failed child must be logged + swallowed as NON-FATAL; script:\n{script}"
    )
    assert "rm -rf" in script and "inflight" in script, (
        "a finished/failed child must release its in-flight lock"
    )


# ===========================================================================
# Watcher scenario 9d737bcd0f4473e9 — startup drain + in-flight dedup
# ===========================================================================

@given(
    'the inbox already holds pending messages that arrived before the watcher '
    'started, and "shop-msg pending inbox --bc shopsystem-messaging" is the '
    "authoritative pending set")
def watch_9d_pending(ctx):
    ctx["watch9d_pending"] = True


@when(
    'the watcher starts and, while a finite child for work id "W" is still in '
    'flight, another wake for the same "W" arrives')
def watch_9d_when(ctx):
    ctx["watch_inspected"] = True


@then(
    "on startup the watcher DRAINS the pre-existing pending inbox by firing a "
    "finite child for each pending work id, so no message that arrived between "
    "sessions is missed")
def watch_9d_drain(ctx):
    script = _script(ctx)
    assert 'shop-msg pending inbox --bc "$BC_NAME"' in script, (
        "the startup drain must query the authoritative pending set "
        f"`shop-msg pending inbox --bc <name>`; script:\n{script}"
    )
    # drain() is invoked at startup (before the supervise/watch loop).
    drain_def = script.find("drain()")
    supervise = script.find("while true; do")
    assert drain_def != -1 and supervise != -1, "drain + supervise must be present"
    # There is an explicit startup `drain` invocation before the watch loop.
    assert "\ndrain\n" in script[:supervise], (
        "the watcher must DRAIN pending inbox on startup before the watch loop; "
        f"script:\n{script}"
    )


@then(
    'while a child for "W" is in flight a second wake for "W" is SKIPPED by '
    "in-flight dedup, so exactly one child runs per work_id concurrently and "
    'duplicate children cannot collide on the shared per-"W" worktree')
def watch_9d_dedup(ctx):
    script = _script(ctx)
    # Atomic mkdir lock per work_id: mkdir succeeds once (spawn), fails while a
    # child holds it (skip).
    assert "mkdir" in script and "inflight" in script, (
        "in-flight dedup must be an atomic `mkdir` lock per work_id; "
        f"script:\n{script}"
    )
    assert "dedup" in script.lower()


@then(
    'once "W"\'s child reaches its terminal its in-flight lock is released, so '
    "a genuinely new later message reusing \"W\" dispatches again")
def watch_9d_lock_release(ctx):
    script = _script(ctx)
    assert "rm -rf" in script and "inflight" in script, (
        "the in-flight lock must be released when the child terminates, so a "
        f"later message reusing the work_id dispatches again; script:\n{script}"
    )
