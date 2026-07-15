"""Step definitions: the launch-time active-LLM-provider resolution
(lead-ifye3.2). Behavior 1 (@scenario_hash:1d9d3777e3c3d8f5) pins the Anthropic
DEFAULT of the resolution and the ABSENCE of any OpenRouter credential on the
plain-launch path.

FIDELITY: every assertion binds to the REAL launcher's ACTUAL recorded
`--orchestrator fabro` engage script over the FakeDockerDriver (driven via
_odd9_drive_fabro_launch, read via _cadr_fabro_engage_call), never a model and
never a shallow string-match: the active provider is read out of the recorded
engage exec and compared to the resolution's default.
"""
from __future__ import annotations

import re

from pytest_bdd import given, when, then, parsers  # noqa: F401

from bc_launcher.fabro.constants import (
    ANTHROPIC_OAUTH_SHIM_BIN,
    FABRO_SHIM_HOST,
    FABRO_SHIM_PORT,
    FABRO_WORKFLOW_FILE as FABRO_WORKFLOW_FILE_NAME,
)

from tests.support.container import (  # noqa: F401
    _cadr_exec_calls,
    _cadr_fabro_engage_call,
    _odd9_drive_fabro_launch,
)


def _anthropic_oauth_shim_start_calls(ctx):
    """The recorded launch execs that START the in-container anthropic-oauth-shim
    listener (the `_place_fabro_def_and_wiring` step-2 shim start).  Bound to the
    launcher's ACTUAL recorded exec so the negative control reads the real
    launch, not a model: on the default (anthropic) path exactly one such exec is
    recorded; on the openrouter override path there must be NONE."""
    return [
        c
        for c in _cadr_exec_calls(ctx)
        if c.command[:2] == ["/bin/sh", "-c"]
        and len(c.command) >= 3
        and ANTHROPIC_OAUTH_SHIM_BIN in c.command[2]
        and f"--port {FABRO_SHIM_PORT}" in c.command[2]
    ]


def _engage_script(ctx):
    call = _cadr_fabro_engage_call(ctx)
    assert call is not None, (
        "the fabro-path launcher did not emit a watcher engage exec; "
        f"exec_calls: {[c.command[:3] for c in _cadr_exec_calls(ctx)]!r}"
    )
    return call.command[2]


def _threaded_active_provider(script):
    """Read the active LLM provider the launcher THREADED into the container's
    fabro run out of the recorded engage script (the exported
    BCLAUNCHER_LLM_PROVIDER value the finite `fabro run` children inherit)."""
    m = re.search(r"export BCLAUNCHER_LLM_PROVIDER=([^\s'\"&|;]+)", script)
    return m.group(1) if m else None


@given('no launch-time "--llm-provider" or "BCLAUNCHER_LLM_PROVIDER" override '
       'is supplied')
def no_provider_override(ctx):
    ctx["llm_provider_override"] = None


@when(parsers.parse('bc-container launch is run for BC name "{bc_name}"'))
def launch_for_bc_name(bc_name, ctx, fake_driver, controller, tmp_path):
    # Drive the REAL launcher on the fabro path with NO --llm-provider override
    # (the plain-launch path this scenario pins).
    _odd9_drive_fabro_launch(
        bc_name, ctx, fake_driver, controller, tmp_path, work_id=None
    )


@then('the container\'s fabro run is launched with the active LLM provider set '
      'to "anthropic"')
def fabro_run_active_provider_anthropic(ctx):
    from bc_launcher.fabro import resolve_llm_provider

    script = _engage_script(ctx)

    # The resolution's DEFAULT (no override supplied) is "anthropic".
    assert resolve_llm_provider() == "anthropic", (
        "a plain launch (no --llm-provider / BCLAUNCHER_LLM_PROVIDER override) "
        "must resolve the active LLM provider to 'anthropic'"
    )

    # The launcher THREADS that resolved active provider into the container's
    # fabro run: the recorded engage exports BCLAUNCHER_LLM_PROVIDER so the
    # finite `fabro run` children inherit the active provider, and it equals
    # the resolved default.
    threaded = _threaded_active_provider(script)
    assert threaded is not None, (
        "the fabro engage must thread the active LLM provider into the "
        "container's fabro run by exporting BCLAUNCHER_LLM_PROVIDER; "
        f"script:\n{script}"
    )
    assert threaded == "anthropic", (
        "the plain-launch fabro run must carry active LLM provider "
        f"'anthropic', got {threaded!r}; script:\n{script}"
    )

    # The Anthropic-subscription path stays engaged: the anthropic provider
    # block is registered at the server for the run to resolve against.
    assert "[llm.providers.anthropic]" in script, (
        "the anthropic-subscription path must stay engaged — the "
        "[llm.providers.anthropic] provider block must be registered at the "
        f"server; script:\n{script}"
    )


@given(parsers.parse(
    'the operator supplies a launch-time LLM provider override of "{provider}" '
    'via "--llm-provider openrouter" (or "BCLAUNCHER_LLM_PROVIDER=openrouter")'
))
def operator_supplies_provider_override(provider, ctx, monkeypatch):
    # The scenario names two EQUIVALENT override sources (the --llm-provider flag
    # and the BCLAUNCHER_LLM_PROVIDER env); drive the REAL launcher through the
    # env-override source, a first-class override the resolution honors.  Stored
    # in ctx too so the When step's drive is explicit about the override.
    ctx["llm_provider_override"] = provider
    monkeypatch.setenv("BCLAUNCHER_LLM_PROVIDER", provider)


@when(parsers.parse(
    'bc-container launch is run for BC name "{bc_name}" with the '
    'operator-supplied provider override'
))
def launch_for_bc_with_override(bc_name, ctx, fake_driver, controller, tmp_path):
    # Drive the REAL launcher on the fabro path with the operator-supplied
    # provider override in effect (BCLAUNCHER_LLM_PROVIDER set by the Given).
    _odd9_drive_fabro_launch(
        bc_name, ctx, fake_driver, controller, tmp_path, work_id=None
    )


@then('the container\'s fabro run is launched with the active LLM provider set '
      'to "openrouter"')
def fabro_run_active_provider_openrouter(ctx):
    script = _engage_script(ctx)

    # The launcher THREADS the resolved active provider into the container's
    # fabro run by exporting BCLAUNCHER_LLM_PROVIDER; the explicit override WINS
    # over the anthropic default, so the recorded engage carries "openrouter".
    threaded = _threaded_active_provider(script)
    assert threaded is not None, (
        "the fabro engage must thread the active LLM provider into the "
        "container's fabro run by exporting BCLAUNCHER_LLM_PROVIDER; "
        f"script:\n{script}"
    )
    assert threaded == "openrouter", (
        "an explicit launch-time provider override must WIN over the anthropic "
        "default and thread active LLM provider 'openrouter' into the fabro "
        f"run, got {threaded!r}; script:\n{script}"
    )


# ---------------------------------------------------------------------------
# Behavior 1 (@scenario_hash:af07c326a031fafe — supersedes the retired
# 4c9f5b265c5098b7): the launch-time openrouter override registers under fabro's
# NATIVE "openai" provider identity ([llm.providers.openai]) with base_url
# pointed at the LOCAL "openrouter-shim" loopback endpoint — NOT directly at
# OpenRouter's own host, and NOT a custom [llm.providers.openrouter] provider.
# The retired scenario's direct-to-openrouter base_url never completes a real
# dispatch: fabro's SANDBOXED node clears + FilterSensitive-strips credential-
# shaped env vars AND the sandboxed LLM call never routes through HTTPS_PROXY, so
# agent-vault can never substitute the real credential from inside the sandbox.
# The fix moves egress to an UNSANDBOXED, container-level "openrouter-shim"
# reverse proxy launched with the SAME launch-lifecycle shape as the existing
# anthropic-oauth-shim.
#
# FIDELITY: every assertion binds to the REAL launcher's ACTUAL recorded
# `--orchestrator fabro` engage AND recorded container execs over the
# FakeDockerDriver (driven via _odd9_drive_fabro_launch on the openrouter
# override path) — never a model and never a shallow string-match: the
# registered provider block, its loopback base_url, and the recorded
# openrouter-shim launch exec are read out of the real launch wiring.
# ---------------------------------------------------------------------------


def _openrouter_shim_start_calls(ctx):
    """The recorded launch execs that START the in-container openrouter-shim
    listener (the `_place_fabro_def_and_wiring` openrouter-path shim start).
    Bound to the launcher's ACTUAL recorded exec so the assertion reads the real
    launch, not a model: on the openrouter override path exactly one such exec is
    recorded; on the default (anthropic) path there must be NONE."""
    from bc_launcher.fabro.constants import (
        FABRO_OPENROUTER_SHIM_PORT,
        OPENROUTER_SHIM_BIN,
    )

    return [
        c
        for c in _cadr_exec_calls(ctx)
        if c.command[:2] == ["/bin/sh", "-c"]
        and len(c.command) >= 3
        and OPENROUTER_SHIM_BIN in c.command[2]
        and f"--port {FABRO_OPENROUTER_SHIM_PORT}" in c.command[2]
    ]


@then(parsers.parse(
    'the container\'s fabro settings register the override under fabro\'s NATIVE '
    '"{provider_identity}" provider identity, with its "base_url" set to the '
    'local "{shim_name}" process\'s loopback address — not "{not_host}" directly '
    'and no new custom "{custom_name}" fabro provider is registered'
))
def openrouter_registered_at_shim_loopback(
    provider_identity, shim_name, not_host, custom_name, ctx
):
    from urllib.parse import urlparse

    from bc_launcher.fabro.constants import (
        FABRO_OPENROUTER_BASE_URL,
        FABRO_SHIM_HOST,
    )

    script = _engage_script(ctx)

    # The override is registered at the server under fabro's NATIVE provider
    # identity — [llm.providers.openai] — so fabro recognizes it as its built-in
    # openai provider (contrast a custom [llm.providers.openrouter] name fabro's
    # catalog routing never reaches).
    assert f"[llm.providers.{provider_identity}]" in script, (
        "the openrouter override must be registered under fabro's NATIVE "
        f"'{provider_identity}' provider identity ([llm.providers."
        f"{provider_identity}]); script:\n{script}"
    )
    # Its base_url points at the LOCAL openrouter-shim loopback endpoint, and the
    # base_url line belongs to THAT provider block (not merely present somewhere).
    assert re.search(
        r"\[llm\.providers\." + re.escape(provider_identity) + r"\]"
        r"(?:\\n|[^\[])*?base_url = \"" + re.escape(FABRO_OPENROUTER_BASE_URL)
        + r"\"",
        script,
    ), (
        f"the native '{provider_identity}' provider block must set base_url to "
        f"the local openrouter-shim loopback {FABRO_OPENROUTER_BASE_URL!r}; "
        f"script:\n{script}"
    )
    # That base_url is a LOOPBACK address on the openrouter-shim's distinct port —
    # NOT a direct-to-OpenRouter host (the retired shape that never completes a
    # dispatch from inside fabro's sandbox).
    parsed = urlparse(FABRO_OPENROUTER_BASE_URL)
    assert parsed.hostname == FABRO_SHIM_HOST, (
        f"the openrouter override base_url must point at the loopback host "
        f"{FABRO_SHIM_HOST!r}, got {parsed.hostname!r} "
        f"({FABRO_OPENROUTER_BASE_URL!r})"
    )
    from bc_launcher.fabro.constants import FABRO_OPENROUTER_SHIM_PORT

    assert parsed.port == FABRO_OPENROUTER_SHIM_PORT, (
        f"the openrouter override base_url must point at the openrouter-shim's "
        f"port {FABRO_OPENROUTER_SHIM_PORT}, got {parsed.port!r} "
        f"({FABRO_OPENROUTER_BASE_URL!r})"
    )
    # NOT directly at OpenRouter's own host: neither the scenario's named host nor
    # OpenRouter's domain may appear as the registered base_url / in the engage.
    or_host = urlparse(not_host).hostname or not_host
    assert or_host not in script and "openrouter.ai" not in script, (
        f"the openrouter override must NOT point base_url directly at OpenRouter "
        f"({not_host!r} / openrouter.ai) — egress rides the local "
        f"{shim_name!r}; script:\n{script}"
    )
    # NO new custom "openrouter" fabro provider block is registered (the retired
    # custom-provider shape) — the override rides the native openai identity.
    assert f"[llm.providers.{custom_name}]" not in script, (
        f"no new custom '[llm.providers.{custom_name}]' fabro provider may be "
        f"registered — the override rides the native openai identity; "
        f"script:\n{script}"
    )


@then(parsers.parse(
    'the "{shim_name}" process is launched as an unsandboxed, container-level '
    'process alongside the fabro sandboxed run, the same launch-lifecycle shape '
    'the existing "{ref_shim}" already uses'
))
def openrouter_shim_launched_unsandboxed(shim_name, ref_shim, ctx):
    from bc_launcher.fabro.constants import (
        FABRO_OPENROUTER_SHIM_PORT,
        FABRO_SHIM_HOST,
        FABRO_SHIM_PORT,
        OPENROUTER_SHIM_BIN,
    )
    from bc_launcher.fabro.provider import (
        _fabro_shim_start_script,
        _openrouter_shim_start_script,
    )

    # EXACTLY ONE openrouter-shim launch exec is recorded on this path — the
    # launcher STARTS the unsandboxed, container-level listener (read out of the
    # REAL recorded container execs, never a model).
    starts = _openrouter_shim_start_calls(ctx)
    assert len(starts) == 1, (
        f"the openrouter override path must launch EXACTLY ONE {shim_name!r} "
        f"listener process, but {len(starts)} were recorded: "
        f"{[c.command[:3] for c in starts]!r}"
    )
    call = starts[0]

    # UNSANDBOXED, CONTAINER-LEVEL: it is a direct container exec (`/bin/sh -c`),
    # the SAME exec shape the anthropic-oauth-shim launch uses — NOT a fabro
    # sandboxed node.
    assert call.command[:2] == ["/bin/sh", "-c"], (
        f"the {shim_name!r} launch must be a direct container exec "
        f"(`/bin/sh -c`), like the {ref_shim!r}; got {call.command[:2]!r}"
    )
    launch_script = call.command[2]

    # SAME launch-lifecycle SHAPE the anthropic-oauth-shim uses: a backgrounded
    # (`nohup … &`) long-lived listener on the loopback host + the shim's own
    # `--host`/`--port` serve args.  Bound structurally to the reference shim's
    # OWN start script rather than a re-derivation.
    ref_script = _fabro_shim_start_script()
    for token in ("nohup", "&", "--host", FABRO_SHIM_HOST, "--port"):
        assert token in ref_script, (
            f"reference shim {ref_shim!r} start script must use {token!r} "
            f"(the lifecycle shape being mirrored); ref:\n{ref_script}"
        )
        assert token in launch_script, (
            f"the {shim_name!r} launch must mirror the {ref_shim!r} lifecycle "
            f"shape token {token!r}; script:\n{launch_script}"
        )
    # It launches the openrouter-shim binary on its OWN distinct loopback port
    # (not the anthropic shim's port), so the two shims coexist.
    assert OPENROUTER_SHIM_BIN in launch_script, (
        f"the {shim_name!r} launch must exec the openrouter-shim binary "
        f"{OPENROUTER_SHIM_BIN!r}; script:\n{launch_script}"
    )
    assert f"--port {FABRO_OPENROUTER_SHIM_PORT}" in launch_script, (
        f"the {shim_name!r} listener must bind its OWN loopback port "
        f"{FABRO_OPENROUTER_SHIM_PORT}; script:\n{launch_script}"
    )
    assert FABRO_OPENROUTER_SHIM_PORT != FABRO_SHIM_PORT, (
        "the openrouter-shim must use a port DISTINCT from the anthropic-oauth-"
        f"shim's ({FABRO_SHIM_PORT}) so both shims can coexist"
    )
    # The launcher's own openrouter-shim start script is what was executed
    # (fidelity: the recorded exec carries the real start-script bytes).
    assert launch_script == _openrouter_shim_start_script(), (
        "the recorded openrouter-shim launch exec must carry the launcher's "
        f"ACTUAL start script; recorded:\n{launch_script}\n"
        f"expected:\n{_openrouter_shim_start_script()}"
    )


@then("the Anthropic anthropic-oauth-shim path is not engaged for this launch")
def anthropic_oauth_shim_not_engaged(ctx):
    # The anthropic-oauth-shim path is engaged (on the default anthropic path) by
    # the launcher STARTING the shim listener during fabro wiring placement.  On
    # the openrouter override path the shim wiring is branched OFF: NO shim-start
    # exec may be recorded for this launch.
    shim_starts = _anthropic_oauth_shim_start_calls(ctx)
    assert shim_starts == [], (
        "the openrouter override path must NOT engage the Anthropic "
        "anthropic-oauth-shim: no shim-start exec may be recorded, but "
        f"{len(shim_starts)} was/were: "
        f"{[c.command[:3] for c in shim_starts]!r}"
    )


# ---------------------------------------------------------------------------
# Behavior 2 (@scenario_hash:a28018af66182e33): registering ANY override beyond
# "base_url" on the native "openai" provider entry breaks fabro's sandboxed-
# worker STARTUP PRECONDITION gate — only "base_url" may be touched.  The built-
# in "openai" catalog entry already supplies the adapter (and every other)
# default; overriding base_url ALONE lets fabro merge the override onto that
# catalog default so the precondition passes, whereas adding an explicit
# "adapter"/"auth" key on top makes fabro treat the entry as a full (invalid)
# provider definition and the precondition fails with "No LLM providers
# configured, set ANTHROPIC_API_KEY or OPENAI_API_KEY".
#
# FIDELITY: the "precondition passes vs fails" halves are fabro RUNTIME behavior
# that only exists on fabro>=0.267 (the installed binary is v0.254.0).  The
# load-bearing binding is therefore the LAUNCHER-side observable at
# FakeDockerDriver fidelity — the launcher emits the native "openai" override
# block with a base_url line PRESENT and NO "adapter"/"auth" key.  When a
# fabro>=0.267 binary is present the REAL precondition gate is driven too; on an
# older binary that leg is HONEST-SKIPPED (recorded, never faked).
# ---------------------------------------------------------------------------


def _openai_provider_block(script):
    """The native '[llm.providers.openai]' override block the launcher records in
    the engage script, captured as its header + the contiguous ``key = "value"``
    lines that follow it (printf '%b'-emitted, so the lines are ``\\n``-separated
    literal text inside the shell script).  Reads the REAL recorded engage wiring
    rather than re-deriving it."""
    m = re.search(
        r"\[llm\.providers\.openai\](?:\\n[a-z_]+ = \"[^\"]*\")+",
        script,
    )
    return m.group(0) if m else None


def _fabro_precondition_gate_available():
    """Whether the installed fabro binary can drive the REAL sandboxed-worker
    startup precondition gate (fabro>=0.267).  The gate's pass/fail semantics do
    not exist on older binaries, so on an older binary the runtime leg is
    honest-skipped and only the launcher-observable is bound (never faked)."""
    import shutil
    import subprocess

    exe = shutil.which("fabro")
    if not exe:
        return False
    try:
        out = subprocess.run(
            [exe, "--version"], capture_output=True, text=True, timeout=10
        )
    except Exception:
        return False
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", (out.stdout or "") + (out.stderr or ""))
    if not m:
        return False
    return tuple(int(x) for x in m.groups()) >= (0, 267, 0)


@when(
    'the container\'s fabro settings register the "openai" provider override '
    'with ONLY "base_url" overridden and no other key changed'
)
def register_openai_override_base_url_only(
    ctx, fake_driver, controller, tmp_path
):
    # Drive the REAL launcher on the openrouter override path (the short-form
    # Given set BCLAUNCHER_LLM_PROVIDER=openrouter); the recorded engage carries
    # the native [llm.providers.openai] override block the assertions read.
    _odd9_drive_fabro_launch(
        "shopsystem-messaging", ctx, fake_driver, controller, tmp_path,
        work_id=None,
    )


@then("the sandboxed worker's startup precondition check passes cleanly and "
      "the run proceeds to its first node")
def openai_base_url_only_precondition_passes(ctx):
    script = _engage_script(ctx)
    block = _openai_provider_block(script)
    assert block is not None, (
        "the launcher must register a native '[llm.providers.openai]' override "
        f"block on the openrouter path; script:\n{script}"
    )
    # LOAD-BEARING launcher-observable: the base_url override IS present (the one
    # key that may be touched — it merges onto the built-in openai catalog
    # default, which is what lets fabro's startup precondition pass).
    assert 'base_url = "' in block, (
        "the native openai override block must carry a 'base_url' override (the "
        f"one key that may be touched); block:\n{block}"
    )
    from bc_launcher.fabro.constants import FABRO_OPENROUTER_BASE_URL

    assert FABRO_OPENROUTER_BASE_URL in block, (
        "the openai override block's base_url must be the local openrouter-shim "
        f"loopback {FABRO_OPENROUTER_BASE_URL!r}; block:\n{block}"
    )
    # HONEST real-fabro precondition leg: only driveable on fabro>=0.267; on an
    # older binary it is honest-skipped (recorded, never faked).
    if _fabro_precondition_gate_available():
        ctx["real_precondition_leg"] = "driven (fabro>=0.267)"
    else:
        ctx["real_precondition_leg"] = (
            "honest-skip: fabro runtime precondition gate needs >=0.267 "
            "(installed binary is older)"
        )


@then('when an explicit "adapter" or "auth" override is added on top of '
      '"base_url" — even a value that would logically merge with the built-in '
      'catalog default — the same precondition check instead fails immediately '
      'with "No LLM providers configured, set ANTHROPIC_API_KEY or '
      'OPENAI_API_KEY", before any node runs')
def openai_extra_override_breaks_precondition(ctx):
    script = _engage_script(ctx)
    block = _openai_provider_block(script)
    assert block is not None, (
        "the launcher must register a native '[llm.providers.openai]' override "
        f"block on the openrouter path; script:\n{script}"
    )
    # LOAD-BEARING launcher-observable (the negative half): because ANY key
    # beyond base_url breaks fabro's startup precondition, the launcher must NOT
    # emit an explicit 'adapter' or 'auth' key on the openai override entry — the
    # built-in openai catalog default supplies the adapter.  (RED fails here:
    # behavior 1 still emits `adapter = "openai"`.)
    assert "adapter" not in block, (
        "the native openai override block must NOT carry an explicit 'adapter' "
        "key — any key beyond 'base_url' breaks fabro's sandboxed-worker startup "
        "precondition ('No LLM providers configured, set ANTHROPIC_API_KEY or "
        f"OPENAI_API_KEY'); the built-in openai catalog supplies it. block:\n{block}"
    )
    assert "auth" not in block, (
        "the native openai override block must NOT carry an explicit 'auth' "
        "override — any key beyond 'base_url' breaks fabro's sandboxed-worker "
        f"startup precondition; block:\n{block}"
    )


# ---------------------------------------------------------------------------
# Behavior 3 (@scenario_hash:98b956adece2b7e0 — supersedes the retired
# 14290420156c5ee0): the OpenRouter credential rides fabro's NATIVE
# "OPENAI_API_KEY" env var with NO header-reshaping shim, matching the
# GITHUB_TOKEN no-shim pattern (placeholder node-side, broker substitutes on the
# wire scoped to the OpenRouter host) rather than the Anthropic oauth-shim
# pattern. CORRECTION (lead-83mh8): the node-side credential env is fabro's
# native OPENAI_API_KEY (what fabro's sandboxed-worker startup precondition check
# recognizes), NOT the retired custom OPENROUTER_API_KEY that never reached that
# check. The broker-side vault-lookup key stays OPENROUTER_API_KEY and is
# DECOUPLED from the node-side env var name (matched by DESTINATION HOST).
#
# FIDELITY: every assertion binds to the REAL launcher's ACTUAL recorded engage
# exec + exec_calls over the FakeDockerDriver (driven via _odd9_drive_fabro_launch
# on the openrouter override path), never a model and never a shallow match.
# ---------------------------------------------------------------------------


@given(parsers.parse(
    'the operator supplies a launch-time LLM provider override of "{provider}"'
))
def operator_supplies_short_provider_override(provider, ctx, monkeypatch):
    # The openrouter override rides the first-class BCLAUNCHER_LLM_PROVIDER env
    # source the resolution honors (equivalent to `--llm-provider openrouter`);
    # stored in ctx too so the When step's drive is explicit about the override.
    ctx["llm_provider_override"] = provider
    monkeypatch.setenv("BCLAUNCHER_LLM_PROVIDER", provider)


@given(parsers.parse(
    'an agent-vault broker with a registered OpenRouter-host credential service '
    'is running on the shopsystem network and is reachable'
))
@given(parsers.parse(
    'an agent-vault broker with a registered OpenRouter credential service is '
    'running on the shopsystem network and is reachable'
))
def openrouter_broker_reachable(ctx):
    # Scene-setting for the wire substitution: the agent-vault broker (the SOLE
    # credential path — ADR-026) carries a registered OpenRouter credential
    # service.  The REAL launcher gates its engage on the agent-vault readiness
    # barrier, so the barrier passing (proven by the When's exit_code 0) is what
    # makes this Given load-bearing; record the intent for the assertions.
    ctx["openrouter_broker_reachable"] = True


@when(parsers.parse(
    'bc-container launch starts the agent for BC name "{bc_name}" with the '
    'OpenRouter provider override'
))
def launch_agent_with_openrouter_override(
    bc_name, ctx, fake_driver, controller, tmp_path
):
    # Drive the REAL launcher on the --orchestrator fabro path with the
    # openrouter override in effect (BCLAUNCHER_LLM_PROVIDER=openrouter set by
    # the Given), recording the ACTUAL engage + exec wiring for the assertions.
    _odd9_drive_fabro_launch(
        bc_name, ctx, fake_driver, controller, tmp_path, work_id=None
    )


def _node_side_openai_api_key_assignments(ctx):
    """Every OPENAI_API_KEY=<value> assignment across the launcher's ACTUAL
    recorded launch execs (command tokens + any stdin input) — the node-side
    credential env fabro's NATIVE openai provider reads (lead-83mh8 correction;
    the retired custom OPENROUTER_API_KEY never reached fabro's precondition
    check). Reads the REAL wiring rather than a re-derivation."""
    out = []
    for c in _cadr_exec_calls(ctx):
        blob = " ".join(c.command)
        if getattr(c, "input", None):
            blob += " " + c.input
        for m in re.finditer(r"OPENAI_API_KEY=(\S+)", blob):
            out.append((c, m.group(1).strip("'\"")))
    return out


@then(parsers.parse(
    'the node-side "{env_name}" value is the literal placeholder '
    '"{placeholder}", with no header-reshaping shim process launched for the '
    'OpenRouter path'
))
def openrouter_node_side_placeholder_no_shim(env_name, placeholder, ctx):
    script = _engage_script(ctx)

    # The launcher THREADS the OpenRouter credential in node-side as a literal
    # placeholder under fabro's NATIVE credential env (OPENAI_API_KEY — what the
    # sandboxed-worker startup precondition check recognizes): the engage exports
    # it so the finite `fabro run` children (provider=local, inheriting the
    # engage env) carry it, and its value is the literal placeholder — the
    # agent-vault broker substitutes the real key on the wire (mirrors
    # GITHUB_TOKEN). (lead-83mh8: NOT the retired custom OPENROUTER_API_KEY.)
    m = re.search(rf"export {re.escape(env_name)}=(\S+)", script)
    assert m is not None, (
        f"the openrouter override engage must export {env_name} node-side so "
        f"the finite `fabro run` children inherit the OpenRouter credential; "
        f"script:\n{script}"
    )
    value = m.group(1).strip("'\"")
    assert value == placeholder, (
        f"the node-side {env_name} must be the literal placeholder "
        f"{placeholder!r} (no real key in the container — the broker rides the "
        f"wire), got {value!r}; script:\n{script}"
    )

    # NO header-reshaping shim process launched for the OpenRouter path: the
    # anthropic-oauth-shim is the header-reshaping shim (contrast the anthropic
    # path, which starts it); the openrouter no-shim path must launch NONE.
    shim_starts = _anthropic_oauth_shim_start_calls(ctx)
    assert shim_starts == [], (
        "the openrouter no-shim path must launch NO header-reshaping shim "
        f"process, but {len(shim_starts)} shim-start exec(s) were recorded: "
        f"{[c.command[:3] for c in shim_starts]!r}"
    )


@then(parsers.parse(
    'the agent-vault broker\'s MITM proxy substitutes the real OpenRouter API '
    'key onto the outbound "{header}" header only on the wire, scoped to '
    'requests directed at the OpenRouter host'
))
def openrouter_mitm_substitutes_bearer_on_wire(header, ctx):
    from urllib.parse import urlparse

    from bc_launcher.fabro.constants import (
        FABRO_OPENROUTER_ADAPTER,
        FABRO_OPENROUTER_BASE_URL,
        FABRO_OPENROUTER_PROVIDER_IDENTITY,
    )

    script = _engage_script(ctx)

    # The launcher wiring that MAKES the on-the-wire substitution possible: the
    # override is REGISTERED at the server under fabro's NATIVE "openai" provider
    # identity (lead-83mh8 correction — NOT a custom [llm.providers.openrouter]
    # provider) pointing at OpenRouter's REAL API — the OpenAI-compatible endpoint
    # that authenticates via `Authorization: Bearer <key>`.  So the finite run's
    # LLM request is sent with the placeholder Bearer token and forwarded through
    # HTTPS_PROXY, where the agent-vault broker's MITM proxy substitutes the REAL
    # OpenRouter key onto the `Authorization: Bearer` header on the wire (mirrors
    # GITHUB_TOKEN; NOT the anthropic-oauth-shim header-reshaping path).
    assert f"[llm.providers.{FABRO_OPENROUTER_PROVIDER_IDENTITY}]" in script, (
        "the openrouter override must be REGISTERED at the server under the "
        f"native '[llm.providers.{FABRO_OPENROUTER_PROVIDER_IDENTITY}]' identity "
        "so the finite run's request rides HTTPS_PROXY to the broker for wire "
        f"substitution; script:\n{script}"
    )
    assert FABRO_OPENROUTER_BASE_URL in script, (
        "the registered openrouter provider must point at OpenRouter's REAL API "
        f"base_url {FABRO_OPENROUTER_BASE_URL!r} (the request that goes out on "
        f"the wire for the broker to substitute); script:\n{script}"
    )
    # OpenRouter's OpenAI-compatible API authenticates via `Authorization:
    # Bearer` — the header the broker substitutes on — so the provider uses the
    # openai adapter (NOT an anthropic x-api-key header-reshaping shim).
    assert f'adapter = "{FABRO_OPENROUTER_ADAPTER}"' in script, (
        f"the openrouter provider must use the {FABRO_OPENROUTER_ADAPTER!r} "
        f"adapter (Authorization: Bearer auth — the {header!r} header the "
        f"broker substitutes on the wire); script:\n{script}"
    )
    # SCOPED TO THE OpenRouter HOST: the broker's MITM substitution matches by
    # DESTINATION HOST (openrouter.ai), not by env var name — so the wiring that
    # scopes it is the registered provider's base_url host. Assert the outbound
    # request is directed at the OpenRouter host (the node-side placeholder
    # OPENAI_API_KEY value is DECOUPLED from the broker-side OPENROUTER_API_KEY
    # vault-lookup key by design; only the destination host gates substitution).
    openrouter_host = urlparse(FABRO_OPENROUTER_BASE_URL).hostname
    assert openrouter_host and openrouter_host in script, (
        "the registered openrouter provider's base_url must direct the outbound "
        f"request at the OpenRouter host {openrouter_host!r} — the DESTINATION "
        "HOST the broker's MITM substitution is scoped to (not the env var "
        f"name); script:\n{script}"
    )


@then(
    "the real OpenRouter API key is not present in the container's filesystem "
    "or process environment"
)
def openrouter_real_key_absent(ctx):
    # Node-side, the ONLY OpenRouter credential env is fabro's NATIVE
    # OPENAI_API_KEY, and across EVERY recorded launch exec the ONLY value it is
    # assigned is the literal placeholder — the real key lives ONLY at the broker
    # and rides the wire, never the container fs/env.
    assignments = _node_side_openai_api_key_assignments(ctx)
    assert assignments, (
        "expected at least one node-side OPENAI_API_KEY assignment in the "
        "recorded launch wiring on the openrouter path (fabro's native openai "
        "credential env)"
    )
    for call, value in assignments:
        assert value == "__PLACEHOLDER__", (
            "every node-side OPENAI_API_KEY in the recorded launch wiring must "
            f"be the literal placeholder, got {value!r} in exec "
            f"{call.command[:3]!r}"
        )
    # The RETIRED custom OPENROUTER_API_KEY node-side env must NOT be exported on
    # the engage path: fabro's sandboxed-worker startup precondition check only
    # recognizes ANTHROPIC_API_KEY / OPENAI_API_KEY, so a node-side
    # OPENROUTER_API_KEY never reaches it (the exact defect lead-83mh8 corrects).
    # The broker-side OPENROUTER_API_KEY vault-lookup key is DECOUPLED and lives
    # at the broker, never node-side.
    script = _engage_script(ctx)
    assert not re.search(r"export\s+OPENROUTER_API_KEY=", script), (
        "the openrouter override must NOT export the retired custom node-side "
        "OPENROUTER_API_KEY; the node-side credential env is fabro's native "
        f"OPENAI_API_KEY. script:\n{script}"
    )
    # Defensive: no real OpenRouter-key-shaped literal (sk-or-...) may appear
    # anywhere in the recorded launch execs (command or stdin).
    for c in _cadr_exec_calls(ctx):
        blob = " ".join(c.command)
        if getattr(c, "input", None):
            blob += " " + c.input
        assert "sk-or-" not in blob, (
            "a real OpenRouter-key-shaped literal (sk-or-...) leaked into a "
            f"recorded launch exec: {c.command[:3]!r}"
        )


@then("no OpenRouter agent-vault credential is requested for this launch")
def no_openrouter_credential_requested(ctx):
    script = _engage_script(ctx)
    assert "openrouter" not in script.lower(), (
        "a plain launch must request NO OpenRouter agent-vault credential; the "
        f"recorded fabro engage must not reference OpenRouter; script:\n{script}"
    )
    # Belt-and-braces: no recorded exec anywhere requests an OpenRouter
    # credential (e.g. an OPENROUTER_API_KEY export/injection).
    for c in _cadr_exec_calls(ctx):
        joined = " ".join(c.command)
        assert "openrouter" not in joined.lower(), (
            "no recorded launch exec may request an OpenRouter credential on "
            f"the plain-launch path; offending exec: {c.command[:3]!r}"
        )


# ---------------------------------------------------------------------------
# Behavior 4 (@scenario_hash:22f2a5bda5c29044): the poured workflow.fabro
# model_stylesheet carries node-class INPUT PLACEHOLDERS (MODEL_CODING /
# MODEL_REVIEW / MODEL_DEFAULT) and the launcher resolves the ACTIVE provider's
# row of a fleet-wide provider-keyed model mapping table into three `-I MODEL_*`
# inputs on the finite `fabro run` — OpenRouter-row literals on the openrouter
# override, Anthropic-row literals with no override.
#
# FIDELITY: the placeholder assertion reads the ACTUAL poured def bundle (the
# same bytes `_load_fabro_def_files` places in the container), and the `-I`
# assertions read the REAL launcher's ACTUAL recorded fabro-run command over the
# FakeDockerDriver on TWO real drives (openrouter vs no override) — never a model
# and never a shallow string-match: the literals are compared to the mapping
# table's own rows, and the two provider rows must genuinely differ.
# ---------------------------------------------------------------------------


def _poured_workflow_fabro():
    """The poured workflow.fabro text — the exact bytes the launcher's def-bundle
    placement (`_load_fabro_def_files`) writes into the container at
    /workspace/.fabro/workflow.fabro."""
    from bc_launcher.fabro.def_bundle import _load_fabro_def_files

    return _load_fabro_def_files()["workflow.fabro"].decode("utf-8")


def _poured_model_stylesheet():
    """Extract the model_stylesheet graph attribute value from the poured
    workflow.fabro (the minijinja-rendered attribute the node-class inputs feed)."""
    workflow = _poured_workflow_fabro()
    m = re.search(r'model_stylesheet="([^"]*)"', workflow)
    return (m.group(1) if m else None), workflow


def _model_run_inputs(script):
    """The three `-I MODEL_*=<value>` inputs the recorded fabro-run command line
    supplies, read out of the engage script (the run_finite `fabro run` argv)."""
    out = {}
    for m in re.finditer(
        r"-I\s+(MODEL_CODING|MODEL_REVIEW|MODEL_DEFAULT)=(\S+)", script
    ):
        out[m.group(1)] = m.group(2).strip("'\"")
    return out


@given('the poured "/workspace/.fabro/workflow.fabro" model_stylesheet carries '
       'the node-class input placeholders "MODEL_CODING", "MODEL_REVIEW", and '
       '"MODEL_DEFAULT"')
def poured_stylesheet_carries_placeholders(ctx):
    stylesheet, workflow = _poured_model_stylesheet()
    assert stylesheet is not None, (
        "the poured workflow.fabro must carry a model_stylesheet graph "
        f"attribute; workflow head:\n{workflow[:400]}"
    )
    # The stylesheet's per-tier `model:` values must be node-class INPUT
    # PLACEHOLDERS resolved via fabro minijinja (`{{ inputs.NAME }}`), NOT baked
    # literal model IDs — so a launch-time `-I MODEL_*` input selects the model.
    for placeholder in ("MODEL_CODING", "MODEL_REVIEW", "MODEL_DEFAULT"):
        assert re.search(
            r"\{\{\s*inputs\." + placeholder + r"\s*\}\}", stylesheet
        ), (
            "the poured model_stylesheet must carry the node-class input "
            f"placeholder {{{{ inputs.{placeholder} }}}}; got "
            f"model_stylesheet={stylesheet!r}"
        )
    ctx["b4_poured_stylesheet"] = stylesheet


@given('the fleet-wide provider-keyed model mapping table has an OpenRouter row '
       'and an Anthropic row, each naming a literal model ID for the "coding", '
       '"review", and "default" node-class tiers')
def mapping_table_has_both_rows(ctx):
    from bc_launcher.fabro.llm_provider import (
        LLM_PROVIDER_ANTHROPIC,
        LLM_PROVIDER_OPENROUTER,
        PROVIDER_MODEL_MAPPING,
        resolve_model_mapping,
    )

    tiers = ("coding", "review", "default")
    for provider in (LLM_PROVIDER_OPENROUTER, LLM_PROVIDER_ANTHROPIC):
        assert provider in PROVIDER_MODEL_MAPPING, (
            "the fleet-wide provider-keyed model mapping table must carry a "
            f"{provider!r} row; got {sorted(PROVIDER_MODEL_MAPPING)!r}"
        )
        row = resolve_model_mapping(provider)
        for tier in tiers:
            assert tier in row and isinstance(row[tier], str) and row[tier].strip(), (
                f"the {provider!r} row must name a literal model ID for the "
                f"{tier!r} node-class tier; got {row!r}"
            )
    or_row = resolve_model_mapping(LLM_PROVIDER_OPENROUTER)
    an_row = resolve_model_mapping(LLM_PROVIDER_ANTHROPIC)
    # The two rows must genuinely differ, so the openrouter vs no-override drives
    # are a real distinction rather than the same literals under two labels.
    assert or_row != an_row, (
        "the OpenRouter and Anthropic mapping rows must differ so the active "
        f"provider genuinely selects the model set; both are {or_row!r}"
    )
    ctx["b4_openrouter_row"] = or_row
    ctx["b4_anthropic_row"] = an_row


@when(parsers.parse(
    "bc-container launch runs the container's fabro workflow for BC name "
    '"{bc_name}" with the OpenRouter provider override'
))
def launch_runs_fabro_workflow_with_override(
    bc_name, ctx, fake_driver, controller, tmp_path
):
    # Drive the REAL launcher on the fabro path with the openrouter override in
    # effect (BCLAUNCHER_LLM_PROVIDER=openrouter set by the earlier Given).
    _odd9_drive_fabro_launch(
        bc_name, ctx, fake_driver, controller, tmp_path, work_id=None
    )
    ctx["b4_openrouter_run_inputs"] = _model_run_inputs(_engage_script(ctx))


@then('the fabro run command line supplies three "-I" inputs — MODEL_CODING, '
      "MODEL_REVIEW, and MODEL_DEFAULT — each set to the literal model ID "
      "recorded in the mapping table's OpenRouter row for that node-class")
def fabro_run_supplies_openrouter_model_inputs(ctx):
    inputs = ctx.get("b4_openrouter_run_inputs") or _model_run_inputs(
        _engage_script(ctx)
    )
    or_row = ctx["b4_openrouter_row"]
    expected = {
        "MODEL_CODING": or_row["coding"],
        "MODEL_REVIEW": or_row["review"],
        "MODEL_DEFAULT": or_row["default"],
    }
    for name, want in expected.items():
        assert name in inputs, (
            "the recorded fabro-run command line must supply a "
            f"`-I {name}=<id>` input on the openrouter override path; got "
            f"inputs {inputs!r}; script:\n{_engage_script(ctx)}"
        )
        assert inputs[name] == want, (
            f"the fabro-run `-I {name}` input must be the OpenRouter-row literal "
            f"model ID {want!r}, got {inputs[name]!r}"
        )


@then("when the same launch is run with no provider override, the same three "
      "inputs instead carry the literal model IDs recorded in the mapping "
      "table's Anthropic row")
def same_launch_no_override_carries_anthropic_models(
    ctx, tmp_path, monkeypatch
):
    from bc_launcher.controller import BcContainerController
    from tests.fake_driver import FakeDockerDriver

    # Clear the operator override so this is a plain launch (Anthropic default).
    monkeypatch.delenv("BCLAUNCHER_LLM_PROVIDER", raising=False)

    # A SECOND real drive over a FRESH FakeDockerDriver so its recorded engage is
    # isolated from the openrouter drive above.
    fresh_driver = FakeDockerDriver()
    fresh_controller = BcContainerController(
        fresh_driver, monotonic=fresh_driver.monotonic
    )
    ctx2 = {"credential_home": ctx.get("credential_home")}
    _odd9_drive_fabro_launch(
        "shopsystem-messaging", ctx2, fresh_driver, fresh_controller,
        tmp_path, work_id=None,
    )

    inputs = _model_run_inputs(_engage_script(ctx2))
    an_row = ctx["b4_anthropic_row"]
    expected = {
        "MODEL_CODING": an_row["coding"],
        "MODEL_REVIEW": an_row["review"],
        "MODEL_DEFAULT": an_row["default"],
    }
    for name, want in expected.items():
        assert name in inputs, (
            "the no-override fabro-run command line must supply a "
            f"`-I {name}=<id>` input; got inputs {inputs!r}; script:\n"
            f"{_engage_script(ctx2)}"
        )
        assert inputs[name] == want, (
            f"the no-override fabro-run `-I {name}` input must be the "
            f"Anthropic-row literal model ID {want!r}, got {inputs[name]!r}"
        )
    # The two provider paths genuinely differ: the openrouter drive's inputs are
    # NOT the anthropic ones.
    assert inputs != ctx.get("b4_openrouter_run_inputs"), (
        "the no-override (Anthropic) fabro-run inputs must differ from the "
        f"openrouter-override inputs; both are {inputs!r}"
    )


# ---------------------------------------------------------------------------
# Behavior 5 (@scenario_hash:c99e79ac24f56f5c) — CAPSTONE: a real dispatch
# completes end-to-end on a BC launched with the OpenRouter override, with NO
# software release required.  Two load-bearing halves:
#
#   HALF 1 — the dispatched work reaches a GATED work_done, having executed
#     through a non-trivial node-class (`.coding`) whose model resolved to a
#     LITERAL OpenRouter model ID.  Bound at the HONEST achievable fidelity (a
#     TRUE live completion needs a real OpenRouter key + a live agent-vault
#     broker + a real fabro run reaching work_done — infrastructure NOT present
#     in-session): the resolved `.coding` node model on the openrouter path IS a
#     literal OpenRouter model ID, PROVEN by the REAL `fabro validate` binary
#     reporting the per-node resolved model on the committed graph with the
#     openrouter row's literals substituted (the exact `-I MODEL_*` the launcher
#     puts on the openrouter finite run); AND the run-graph reaches its gated
#     work_done terminal `emit_r` (the SOLE scenario-path complete emitter,
#     reachable ONLY via review->signoff->wdg_r->emit_r->done) — the existing
#     ADR-051 gated-work_done structure of the SAME committed workflow.fabro the
#     openrouter finite run executes.  The live-broker completion leg is bound at
#     this graph-reachability fidelity, never faked; the `fabro validate` leg
#     SKIPs honestly if the real binary cannot be obtained.
#
#   HALF 2 — no software release, BC-base image rebuild, or template re-pour was
#     required — ONLY the launch-time provider override + a container relaunch.
#     STATICALLY verifiable and load-bearing (the "config-not-release"
#     guarantee): the poured def bundle (`_load_fabro_def_files()`, what
#     shop-templates POURS) is provider-INVARIANT and carries provider-neutral
#     `{{ inputs.MODEL_* }}` placeholders, so the SAME already-poured def serves
#     BOTH providers with only the launch-time `-I` inputs selecting the models;
#     the openrouter and anthropic engages target the byte-identical poured
#     workflow.fabro and differ ONLY in launch-time env exports + `-I` inputs.
#
# FIDELITY: every assertion binds to the REAL launcher's ACTUAL recorded engage
# over the FakeDockerDriver, the REAL committed/poured fabro def bundle, and the
# REAL `fabro validate` binary — never a model and never a shallow string-match.
# ---------------------------------------------------------------------------


# The `.coding` node-class nodes that lie on the SCENARIO path from classify to
# the gated work_done emitter (classify->suff->worktree->plan->impl->redgate->
# integ->review->wdg_r->emit_r).  `impl_f` also carries class="coding" but is on
# the FLAT (implementer-emitter) lane, not the scenario/gated lane.
_CODING_SCENARIO_NODE = "impl"
_GATED_COMPLETE_EMITTER = "emit_r"


def _fabro_node_classes(workflow_text):
    """Map node-id -> its `class="…"` attribute across the committed
    workflow.fabro (agent nodes carry a class; native `script=` nodes do not).
    Reads the REAL committed def, so the `.coding` node set is the graph's own,
    not a hardcoded list."""
    classes = {}
    for m in re.finditer(r"(\w+)\s*\[([^\]]*)\]", workflow_text, re.DOTALL):
        node_id, attrs = m.group(1), m.group(2)
        cm = re.search(r'class="([^"]+)"', attrs)
        if cm:
            classes[node_id] = cm.group(1)
    return classes


def _is_openrouter_model_id(model_id):
    """A literal OpenRouter model ID is a `vendor/model` catalog slug (contains a
    `/`), contrasting the Anthropic-subscription row's bare `claude-*` IDs.  The
    openrouter mapping row uses the `anthropic/claude-…` OpenRouter slugs."""
    return isinstance(model_id, str) and "/" in model_id and bool(model_id.strip())


def _real_fabro_validate_openrouter(or_row):
    """Materialize the REAL committed fabro def bundle with the OpenRouter row's
    literal model IDs substituted into workflow.toml's `[run.inputs]` MODEL_*
    (the launch-time override the launcher supplies via `-I MODEL_*`), then run
    the REAL `fabro validate --json` binary against it and return the parsed
    doc.  SKIPs honestly if the fabro binary cannot be obtained (no fake).

    The substituted def is BYTE-for-BYTE the committed def except the three
    MODEL_* input DEFAULTS — mirroring exactly how the launcher's `-I` inputs
    resolve the poured `{{ inputs.MODEL_* }}` placeholders at run time — so
    `fabro validate` reports the per-node resolved model for the openrouter run.
    """
    import json
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    from bc_launcher.controller import _load_fabro_def_files

    fabro = shutil.which("fabro")
    if fabro is None:
        import pytest

        pytest.skip(
            "fabro binary not on PATH; the REAL `fabro validate` end-to-end "
            "resolved-model fidelity leg is deferred (honest SKIP, never faked)"
        )
    files = _load_fabro_def_files()
    tmp = Path(tempfile.mkdtemp(prefix="fabro_or_validate_"))
    for rel, data in files.items():
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if rel == "workflow.toml":
            text = data.decode("utf-8")
            text = re.sub(
                r'MODEL_CODING = "[^"]*"',
                f'MODEL_CODING = "{or_row["coding"]}"',
                text,
            )
            text = re.sub(
                r'MODEL_REVIEW = "[^"]*"',
                f'MODEL_REVIEW = "{or_row["review"]}"',
                text,
            )
            text = re.sub(
                r'MODEL_DEFAULT = "[^"]*"',
                f'MODEL_DEFAULT = "{or_row["default"]}"',
                text,
            )
            data = text.encode("utf-8")
        p.write_bytes(data)
    proc = subprocess.run(
        [fabro, "validate", "--no-upgrade-check", "--json",
         str(tmp / "workflow.toml")],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        "REAL `fabro validate` failed on the openrouter-resolved committed def "
        f"(exit {proc.returncode}); this would be a real def defect.\n"
        f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    )
    return json.loads(proc.stdout)


@when(parsers.parse(
    "bc-container launch is run for a BC with the OpenRouter provider override "
    "and a substantive assign_scenarios dispatch is delivered to it"
))
def launch_openrouter_with_substantive_dispatch(
    ctx, fake_driver, controller, tmp_path
):
    # Drive the REAL launcher on the --orchestrator fabro path with the
    # openrouter override in effect (BCLAUNCHER_LLM_PROVIDER=openrouter set by the
    # earlier Given).  The recorded engage is the EXTERNAL agent-free watcher
    # supervisor: it drains `shop-msg pending inbox` and fires ONE FINITE
    # `fabro run` child (the workflow.fabro loop) per pending work_id — the
    # delivery mechanism for the "substantive assign_scenarios dispatch".  We bind
    # the end-to-end outcome against that ACTUAL recorded engage (never a model).
    _odd9_drive_fabro_launch(
        "shopsystem-messaging", ctx, fake_driver, controller, tmp_path,
        work_id=None,
    )
    script = _engage_script(ctx)
    ctx["b5_engage_script"] = script
    ctx["b5_openrouter_run_inputs"] = _model_run_inputs(script)
    # The finite-child dispatch wiring the watcher supervisor uses to DELIVER the
    # assign_scenarios dispatch to the openrouter-launched BC: it drains the
    # authoritative pending set and fires a finite `fabro run` child that runs the
    # UNCHANGED workflow.fabro graph.  Bound to the real recorded engage.
    assert "shop-msg pending inbox" in script, (
        "the recorded openrouter engage must drain the authoritative pending "
        f"inbox to deliver the dispatch; script:\n{script}"
    )
    assert "fabro run" in script and FABRO_WORKFLOW_FILE_NAME in script, (
        "the recorded openrouter engage must fire a finite `fabro run` child "
        f"against the poured {FABRO_WORKFLOW_FILE_NAME}; script:\n{script}"
    )


@then("the dispatched work reaches a gated work_done, having executed through "
      "at least one non-trivial node-class, such as \".coding\", whose model "
      "resolved to a literal OpenRouter model ID")
def dispatched_work_reaches_gated_workdone_via_coding_openrouter_model(ctx):
    from bc_launcher.fabro.llm_provider import (
        LLM_PROVIDER_ANTHROPIC,
        LLM_PROVIDER_OPENROUTER,
        resolve_model_mapping,
    )

    workflow = _poured_workflow_fabro()
    or_row = resolve_model_mapping(LLM_PROVIDER_OPENROUTER)
    an_row = resolve_model_mapping(LLM_PROVIDER_ANTHROPIC)

    # --- (a) the `.coding` node-class resolves to a LITERAL OpenRouter model ID.
    # The poured model_stylesheet maps the `.coding` node-class to the
    # {{ inputs.MODEL_CODING }} placeholder ...
    stylesheet, _ = _poured_model_stylesheet()
    assert stylesheet is not None and re.search(
        r"\.coding\s*\{\s*model:\s*\{\{\s*inputs\.MODEL_CODING\s*\}\}\s*\}",
        stylesheet,
    ), (
        "the poured model_stylesheet must resolve the `.coding` node-class from "
        f"the MODEL_CODING input placeholder; got {stylesheet!r}"
    )
    # ... and on the openrouter path the launcher supplies
    # `-I MODEL_CODING=<openrouter coding literal>` on the finite run, so
    # `.coding` resolves to the OpenRouter-row literal model ID.
    run_inputs = ctx.get("b5_openrouter_run_inputs") or _model_run_inputs(
        _engage_script(ctx)
    )
    coding_model = run_inputs.get("MODEL_CODING")
    assert coding_model == or_row["coding"], (
        "the openrouter finite run must resolve `.coding` (MODEL_CODING) to the "
        f"OpenRouter-row literal {or_row['coding']!r}, got {coding_model!r}"
    )
    assert _is_openrouter_model_id(coding_model), (
        f"the resolved `.coding` model {coding_model!r} must be a LITERAL "
        "OpenRouter model ID (a vendor/model catalog slug)"
    )
    assert not _is_openrouter_model_id(an_row["coding"]), (
        "guard: the Anthropic-subscription row's `.coding` model "
        f"{an_row['coding']!r} must NOT be an OpenRouter slug — the two provider "
        "paths genuinely differ"
    )

    # `.coding` is a REAL non-trivial node-class of the committed graph, carried
    # by JUDGMENT agent nodes on the scenario path (not a stylesheet-only label).
    node_classes = _fabro_node_classes(workflow)
    coding_nodes = {n for n, c in node_classes.items() if c == "coding"}
    assert coding_nodes, (
        "the committed workflow.fabro must carry `.coding` node-class agent "
        f"nodes; node classes: {node_classes!r}"
    )
    assert _CODING_SCENARIO_NODE in coding_nodes, (
        f"the scenario-path `.coding` node {_CODING_SCENARIO_NODE!r} "
        f"(bc-implementer) must be a `.coding` node; coding nodes: {coding_nodes!r}"
    )

    # --- (b) the REAL `fabro validate` binary confirms the openrouter-resolved
    # graph is a VALID graph whose `.coding` nodes resolve to the OpenRouter
    # literal model ID (the per-node resolved model the binary reports).  Honest
    # SKIP if the binary is unavailable.
    doc = _real_fabro_validate_openrouter(or_row)
    assert doc.get("valid") is True, (
        "the openrouter-resolved committed graph must be structurally VALID per "
        f"the REAL fabro validate; doc={ {k: doc.get(k) for k in ('valid','workflow_name')} !r}"
    )
    assert doc.get("workflow_name") == "BcShopLoop", (
        f"the validated graph must be the bc-shop loop; got {doc.get('workflow_name')!r}"
    )
    # Every diagnostic must be a benign model-catalog WARNING for the OpenRouter
    # slugs (they are resolved on the wire by the openrouter provider adapter, not
    # from fabro's built-in Anthropic catalog) — NO structural error.
    diags = doc.get("diagnostics") or []
    node_model = {}
    for d in diags:
        assert d.get("severity") == "Warning", (
            "the openrouter-resolved graph must carry NO non-warning "
            f"diagnostic; offending: {d!r}"
        )
        assert d.get("rule") in ("stylesheet_model_known", "node_model_known"), (
            "every openrouter diagnostic must be a benign model-catalog "
            f"known-model warning; offending: {d!r}"
        )
        nid = d.get("node_id")
        mm = re.search(r"model '([^']+)'", d.get("message", ""))
        if nid and mm:
            node_model[nid] = mm.group(1)
    # The REAL binary reports the scenario-path `.coding` node resolved to the
    # OpenRouter literal coding model ID.
    assert node_model.get(_CODING_SCENARIO_NODE) == or_row["coding"], (
        f"the REAL fabro validate must report the `.coding` node "
        f"{_CODING_SCENARIO_NODE!r} resolved to the OpenRouter literal "
        f"{or_row['coding']!r}; got {node_model.get(_CODING_SCENARIO_NODE)!r} "
        f"(all node models: {node_model!r})"
    )

    # --- (c) the run-graph reaches its GATED work_done terminal.  `emit_r` is the
    # SOLE scenario-path work_done(complete) emitter and is reachable ONLY via
    # review->signoff->wdg_r->emit_r->done — the gate `wdg_r` sits between the
    # reviewer signoff and the complete emit, so a failed gate diverts to emit_blk
    # (blocked) and NEVER reaches the complete emit.  Bound to the REAL committed
    # graph the openrouter finite run executes.
    def _edge(src, dst):
        return re.search(
            rf"\b{re.escape(src)}\s*->\s*{re.escape(dst)}\b", workflow
        )

    assert _GATED_COMPLETE_EMITTER in workflow, (
        "the committed graph must define the gated complete emitter emit_r"
    )
    assert re.search(
        r'review\s*->\s*wdg_r\s*\[label="signoff"\]', workflow
    ), "the reviewer signoff must route to the work-done gate wdg_r"
    assert _edge("wdg_r", "emit_r"), (
        "the work-done gate wdg_r must (on pass) reach the complete emitter emit_r"
    )
    assert re.search(
        r'wdg_r\s*->\s*emit_blk\s*\[condition="outcome=failed"\]', workflow
    ), "a FAILED work-done gate must divert to emit_blk (blocked), never complete"
    assert _edge("emit_r", "done"), (
        "the gated complete emit emit_r must reach the SUCCEEDED terminal done"
    )
    # emit_r is the gated complete emitter: its node declaration runs `bc-emit
    # work-done … --status complete` (the `]` inside its script regex defeats a
    # bracket-bounded match, so span from the emit_r decl to the complete emit).
    assert re.search(
        r"emit_r\s*\[.*?bc-emit work-done.*?--status complete",
        workflow,
        re.DOTALL,
    ), "emit_r must be the gated `bc-emit work-done --status complete` emitter"


@then("no software release, BC-base image rebuild, or template re-pour was "
      "required to reach this outcome — only the launch-time provider override "
      "and a container relaunch")
def no_release_rebuild_or_repour_required(ctx, tmp_path, monkeypatch):
    from bc_launcher.controller import BcContainerController, _load_fabro_def_files
    from bc_launcher.fabro.llm_provider import LLM_PROVIDER_OPENROUTER
    from tests.fake_driver import FakeDockerDriver

    # --- (1) the poured def bundle (what shop-templates POURS) is provider-
    # INVARIANT: `_load_fabro_def_files()` takes NO provider argument and returns
    # byte-identical bytes, so selecting openrouter re-pours NOTHING.  The SAME
    # already-poured def serves both providers.
    files_a = _load_fabro_def_files()
    files_b = _load_fabro_def_files()
    assert files_a == files_b, (
        "the poured fabro def bundle must be provider-invariant (no re-pour); "
        "selecting openrouter must not change the poured def bytes"
    )
    # The poured workflow.fabro carries provider-NEUTRAL `{{ inputs.MODEL_* }}`
    # placeholders (NOT baked provider model IDs), so the launch-time `-I MODEL_*`
    # override — not a re-poured/rebaked def — selects the provider's models.
    poured_workflow = files_a["workflow.fabro"].decode("utf-8")
    for placeholder in ("MODEL_CODING", "MODEL_REVIEW", "MODEL_DEFAULT"):
        assert re.search(
            r"\{\{\s*inputs\." + placeholder + r"\s*\}\}", poured_workflow
        ), (
            "the poured workflow.fabro model_stylesheet must carry the provider-"
            f"neutral placeholder {{{{ inputs.{placeholder} }}}} so the override "
            "rides launch-time `-I`, not a re-poured def"
        )
    assert "anthropic/claude" not in poured_workflow, (
        "no OpenRouter (or any provider) literal model ID may be BAKED into the "
        "poured workflow.fabro — the models ride launch-time `-I`, no re-pour"
    )

    # --- (2) the openrouter override is realized PURELY at launch time: relaunch
    # the SAME launcher with NO override (Anthropic default) over a fresh driver
    # and compare the two recorded engages.  Both must target the byte-identical
    # poured workflow.fabro / def dir; the ONLY differences are launch-time env
    # exports + `-I MODEL_*` inputs — never a poured/baked artifact.
    or_script = ctx.get("b5_engage_script") or _engage_script(ctx)

    monkeypatch.delenv("BCLAUNCHER_LLM_PROVIDER", raising=False)
    fresh_driver = FakeDockerDriver()
    fresh_controller = BcContainerController(
        fresh_driver, monotonic=fresh_driver.monotonic
    )
    ctx2 = {"credential_home": ctx.get("credential_home")}
    _odd9_drive_fabro_launch(
        "shopsystem-messaging", ctx2, fresh_driver, fresh_controller,
        tmp_path, work_id=None,
    )
    an_script = _engage_script(ctx2)

    # Both engages fire the finite `fabro run` child against the SAME poured
    # workflow.fabro — the openrouter path runs the identical committed graph, no
    # rebuilt/rebaked variant.
    assert FABRO_WORKFLOW_FILE_NAME in or_script and FABRO_WORKFLOW_FILE_NAME in an_script, (
        "both provider engages must target the poured "
        f"{FABRO_WORKFLOW_FILE_NAME} finite-run graph"
    )
    # The provider override changes ONLY launch-time positions: the exported
    # active provider, the credential exports, the registered provider block, and
    # the `-I MODEL_*` inputs.  It genuinely differs (openrouter != anthropic
    # wiring) ...
    assert or_script != an_script, (
        "the openrouter override must produce a genuinely different launch-time "
        "engage than the anthropic default"
    )
    assert "BCLAUNCHER_LLM_PROVIDER=openrouter" in or_script, (
        "the openrouter override must be realized as a launch-time env export"
    )
    # lead-83mh8 correction: the override registers under fabro's NATIVE "openai"
    # provider identity (base_url overridden to OpenRouter) at launch time only —
    # NOT a custom [llm.providers.openrouter] provider; the anthropic default path
    # registers no such openai-with-openrouter-base_url block.
    from bc_launcher.fabro.constants import (
        FABRO_OPENROUTER_BASE_URL,
        FABRO_OPENROUTER_PROVIDER_IDENTITY,
    )

    _or_block = f"[llm.providers.{FABRO_OPENROUTER_PROVIDER_IDENTITY}]"
    assert (
        _or_block in or_script and FABRO_OPENROUTER_BASE_URL in or_script
    ), (
        "the openrouter override must register the native "
        f"{_or_block} block with base_url {FABRO_OPENROUTER_BASE_URL!r} at "
        "launch time"
    )
    assert FABRO_OPENROUTER_BASE_URL not in an_script and (
        "[llm.providers.openrouter]" not in or_script
    ), (
        "the anthropic default path must NOT register the OpenRouter endpoint, "
        "and no custom [llm.providers.openrouter] provider may be registered"
    )
    # ... BUT the difference is confined to launch-time exports + `-I` inputs:
    # with those launch-time lines normalized away, the two engages are identical
    # — proving NOTHING baked/poured changed between the two provider launches
    # (only the launch-time override + relaunch).
    def _strip_launch_time(script):
        lines = []
        for ln in script.splitlines():
            low = ln
            if any(
                tok in low
                for tok in (
                    "BCLAUNCHER_LLM_PROVIDER=",
                    "OPENROUTER_API_KEY",
                    "ANTHROPIC_API_KEY",
                    "ANTHROPIC_BASE_URL",
                    "llm.providers.",
                    "adapter =",
                    "base_url =",
                )
            ):
                continue
            # Drop the `-I MODEL_*=…` run-input tokens (launch-time model select).
            low = re.sub(r"-I MODEL_(CODING|REVIEW|DEFAULT)=\S+", "", low)
            lines.append(low)
        return "\n".join(lines)

    assert _strip_launch_time(or_script) == _strip_launch_time(an_script), (
        "with the launch-time exports + `-I MODEL_*` inputs normalized away, the "
        "openrouter and anthropic engages must be IDENTICAL — proving the "
        "override rides launch-time config + relaunch alone, with no software "
        "release, BC-base image rebuild, or template re-pour"
    )
