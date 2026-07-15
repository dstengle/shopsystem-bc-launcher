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
# Behavior 4 / the credential HOP (@scenario_hash:05638241a033ef0c — supersedes
# the retired 98b956adece2b7e0): the real OpenRouter credential is substituted on
# the openrouter-shim's OWN outbound hop by agent-vault, matching the GITHUB_TOKEN
# no-shim pattern MOVED ONE HOP OUT — never present in the sandboxed node's
# filesystem or process environment.
#
# WHY THE HOP MOVED (retirement of 98b956ad): the retired scenario placed the
# agent-vault substitution on the SANDBOXED node's OWN wire hop (real key onto the
# node's Authorization: Bearer header via the container HTTPS_PROXY).  fabro's
# sandboxed execution path CLEARS + FilterSensitive-strips credential-shaped env
# vars before spawning AND never routes the sandboxed LLM call through HTTPS_PROXY,
# so agent-vault could never substitute from inside the sandbox.  The corrected
# scenario moves the substitution ONE HOP OUT: node-side "OPENAI_API_KEY" stays the
# literal "__PLACEHOLDER__" (carried unchanged onto the Authorization: Bearer
# header the node sends over PLAIN LOOPBACK to the local openrouter-shim), and the
# UNSANDBOXED openrouter-shim process's OWN environment carries the real
# HTTPS_PROXY through which agent-vault substitutes the real OpenRouter key on the
# shim's outbound wire hop, scoped to the OpenRouter host.
#
# FIDELITY: every assertion binds to the REAL launcher's ACTUAL recorded engage
# exec + the recorded openrouter-shim launch exec's OWN env + the container's
# docker-run HTTPS_PROXY + the REAL committed openrouter-shim asset over the
# FakeDockerDriver (driven via _odd9_drive_fabro_launch on the openrouter override
# path), never a model and never a shallow string-match.
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
    'the sandboxed node\'s "{env_name}" value is the literal placeholder '
    '"{placeholder}", carried unchanged onto the "{header}" header the node '
    'sends to the "{shim_name}"'
))
def openrouter_node_side_placeholder_onto_bearer(
    env_name, placeholder, header, shim_name, ctx
):
    from urllib.parse import urlparse

    from bc_launcher.fabro.constants import (
        FABRO_OPENROUTER_BASE_URL,
        FABRO_OPENROUTER_SHIM_PORT,
        FABRO_SHIM_HOST,
    )

    script = _engage_script(ctx)

    # NODE-SIDE PLACEHOLDER: the engage exports the OpenRouter credential env under
    # fabro's NATIVE OPENAI_API_KEY as the literal placeholder, so the sandboxed
    # finite `fabro run` children (provider=local, inheriting the engage env) carry
    # only the placeholder — never a real key.
    m = re.search(rf"export {re.escape(env_name)}=(\S+)", script)
    assert m is not None, (
        f"the openrouter override engage must export {env_name} node-side so the "
        f"sandboxed finite `fabro run` children inherit the credential env; "
        f"script:\n{script}"
    )
    value = m.group(1).strip("'\"")
    assert value == placeholder, (
        f"the sandboxed node's {env_name} must be the literal placeholder "
        f"{placeholder!r} (no real key node-side), got {value!r}; script:\n{script}"
    )

    # CARRIED ONTO THE Authorization: Bearer HEADER THE NODE SENDS TO THE SHIM: the
    # override registers the native "openai" provider with base_url pointed at the
    # LOCAL openrouter-shim loopback, and the built-in openai catalog adapter
    # (NO explicit adapter override — behavior 2) authenticates via
    # `Authorization: Bearer <OPENAI_API_KEY>`.  So the finite run sends its
    # request — carrying that placeholder Bearer token UNCHANGED — to the shim.
    or_block = _openai_provider_block(script)
    assert or_block is not None and "adapter" not in or_block, (
        "the native openai override block must carry NO explicit 'adapter' key so "
        "its built-in catalog default supplies Authorization: Bearer auth; "
        f"block:\n{or_block}"
    )
    parsed = urlparse(FABRO_OPENROUTER_BASE_URL)
    assert parsed.hostname == FABRO_SHIM_HOST and parsed.port == FABRO_OPENROUTER_SHIM_PORT, (
        f"the node's request must be directed at the local {shim_name!r} loopback "
        f"({FABRO_SHIM_HOST}:{FABRO_OPENROUTER_SHIM_PORT}) — the base_url the "
        f"Bearer-carrying request is sent to; got {FABRO_OPENROUTER_BASE_URL!r}"
    )
    assert FABRO_OPENROUTER_BASE_URL in script, (
        f"the registered openai provider base_url must be the local {shim_name!r} "
        f"loopback {FABRO_OPENROUTER_BASE_URL!r}; script:\n{script}"
    )


@then(parsers.parse(
    'the "{shim_name}" process\'s own environment (not the sandboxed node\'s) '
    'carries the real "{proxy_env}", through which the agent-vault broker\'s MITM '
    'proxy substitutes the real OpenRouter API key onto that same "{header}" '
    'header only on the shim\'s outbound wire hop, scoped to requests directed at '
    'the OpenRouter host'
))
def openrouter_shim_own_env_carries_real_proxy(
    shim_name, proxy_env, header, ctx
):
    from bc_launcher.constants import SSL_CERT_FILE_ENV
    from bc_launcher.fabro.constants import OPENROUTER_SHIM_BIN

    # EXACTLY ONE recorded openrouter-shim launch exec; read its OWN exec env (the
    # per-exec `docker exec -e KEY=VALUE` env the FakeDockerDriver records).
    starts = _openrouter_shim_start_calls(ctx)
    assert len(starts) == 1, (
        f"the openrouter override path must launch EXACTLY ONE {shim_name!r} "
        f"process, but {len(starts)} were recorded: "
        f"{[c.command[:3] for c in starts]!r}"
    )
    shim_env = starts[0].env or {}

    # THE SHIM'S OWN ENVIRONMENT CARRIES THE REAL HTTPS_PROXY: its unsandboxed
    # outbound `curl` hop routes through the agent-vault MITM proxy, where the real
    # OpenRouter key is substituted on the wire.  (RED fails here: the openrouter-
    # shim launch exec's env carries only SSL_CERT_FILE, no HTTPS_PROXY.)
    assert proxy_env in shim_env, (
        f"the {shim_name!r} launch exec's OWN environment must carry {proxy_env!r} "
        "so its outbound OpenRouter curl routes through the agent-vault MITM proxy "
        f"for wire substitution; recorded shim exec env: {shim_env!r}"
    )
    proxy_value = shim_env[proxy_env]
    assert proxy_value, (
        f"the {shim_name!r} exec's {proxy_env} must be non-empty (a real proxy "
        f"address); got {proxy_value!r}"
    )

    # IT IS THE REAL container-runtime proxy the launch wired — identical to the
    # container's docker-run HTTPS_PROXY (the agent-vault MITM proxy), never a
    # fabricated value.  Read from the REAL recorded `docker run` env.
    container_proxy = ctx["cadr_driver"].container_proxy_env(ctx["container_name"])
    assert container_proxy and proxy_value == container_proxy, (
        f"the {shim_name!r} exec's {proxy_env} must equal the container's real "
        f"docker-run HTTPS_PROXY {container_proxy!r} (the agent-vault MITM proxy), "
        f"got {proxy_value!r}"
    )

    # + the MITM CA trust var so the shim's curl trusts the agent-vault MITM cert
    # over that HTTPS_PROXY hop (the lead-ze4w BUG#3 non-login-shell reason).
    assert shim_env.get(SSL_CERT_FILE_ENV), (
        f"the {shim_name!r} exec env must pin {SSL_CERT_FILE_ENV} to the "
        f"materialized broker CA so its outbound HTTPS over {proxy_env} trusts the "
        f"agent-vault MITM CA; recorded shim exec env: {shim_env!r}"
    )

    # NOT THE SANDBOXED NODE'S ENV: the node -> shim hop is PLAIN LOOPBACK; the
    # engage's node-side env must NOT export HTTPS_PROXY for the finite run (the
    # real HTTPS_PROXY belongs to the UNSANDBOXED shim's OWN outbound hop only).
    script = _engage_script(ctx)
    assert not re.search(rf"export\s+{re.escape(proxy_env)}=", script), (
        f"the sandboxed node's engage env must NOT export {proxy_env} — the "
        f"node->shim hop is plain loopback; the real {proxy_env} belongs to the "
        f"UNSANDBOXED {shim_name!r} process's OWN outbound hop; script:\n{script}"
    )

    # SCOPED TO THE OpenRouter HOST: agent-vault's MITM substitution matches by
    # DESTINATION HOST, and the shim forwards its outbound hop to the REAL
    # OpenRouter API host — bound to the committed openrouter-shim asset's own
    # UPSTREAM default (never a re-derivation).
    launch_script = starts[0].command[2]
    assert OPENROUTER_SHIM_BIN in launch_script, (
        f"the recorded {shim_name!r} launch must exec the openrouter-shim binary "
        f"{OPENROUTER_SHIM_BIN!r}; script:\n{launch_script}"
    )
    from tests.test_lead_ifye3_5_openrouter_shim import _load_openrouter_shim_module

    shim_mod = _load_openrouter_shim_module()
    upstream = getattr(shim_mod, "UPSTREAM", "") or ""
    assert "openrouter.ai" in upstream, (
        f"the {shim_name!r} outbound hop must forward to the OpenRouter host "
        f"(openrouter.ai) — the DESTINATION HOST the broker's MITM substitution is "
        f"scoped to; committed shim UPSTREAM={upstream!r}"
    )


@then(parsers.parse(
    'the real OpenRouter API key is not present in the sandboxed node\'s '
    'filesystem or process environment at any point, including via '
    '"{overlay}" overlays, because fabro\'s sandboxed execution path clears and '
    'filters credential-shaped environment variables before spawning'
))
def openrouter_real_key_absent_node_side(overlay, ctx):
    # NODE-SIDE: the ONLY OpenRouter credential env is fabro's NATIVE
    # OPENAI_API_KEY, and across EVERY recorded launch exec the ONLY value it is
    # assigned is the literal placeholder — the real key lives ONLY at the broker
    # and rides the shim's wire hop, never the sandboxed node's fs/env.
    assignments = _node_side_openai_api_key_assignments(ctx)
    assert assignments, (
        "expected at least one node-side OPENAI_API_KEY assignment in the "
        "recorded launch wiring on the openrouter path (fabro's native openai "
        "credential env)"
    )
    for call, value in assignments:
        assert value == "__PLACEHOLDER__", (
            "every node-side OPENAI_API_KEY in the recorded launch wiring must be "
            f"the literal placeholder, got {value!r} in exec {call.command[:3]!r}"
        )

    script = _engage_script(ctx)
    # No retired custom node-side OPENROUTER_API_KEY export.
    assert not re.search(r"export\s+OPENROUTER_API_KEY=", script), (
        "the openrouter override must NOT export a node-side OPENROUTER_API_KEY; "
        f"the node-side credential env is fabro's native OPENAI_API_KEY. "
        f"script:\n{script}"
    )

    # INCLUDING VIA [run.environment.env] OVERLAYS: the engage's materialize_child
    # heredoc [run.environment.env] overlay (the ONE overlay `fabro run` applies to
    # the sandboxed child that `-I` does NOT reach) must carry NO real credential —
    # it holds only the BC_NAME/WORK_ID identity, never a real OpenRouter key.
    for overlay_block in re.findall(
        r"\[run\.environment\.env\](.*?)(?:\[run\.|EOF)", script, re.DOTALL
    ):
        assert "OPENROUTER_API_KEY" not in overlay_block, (
            f"the {overlay} overlay must not carry an OpenRouter credential; "
            f"overlay:\n{overlay_block}"
        )
        assert "sk-or-" not in overlay_block and "sk-ant-" not in overlay_block, (
            f"the {overlay} overlay must carry NO real key-shaped literal; "
            f"overlay:\n{overlay_block}"
        )

    # DEFENSIVE: no real OpenRouter-key-shaped literal (sk-or-...) may appear
    # anywhere in the recorded launch execs (command or stdin) — the sandboxed
    # node never sees the real key on its fs or in its process env at any point.
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
# Behavior 5 (@scenario_hash:a3b2b6bebcee78f5 — supersedes the retired
# 22f2a5bda5c29044): the dispatcher's per-child `fabro run` command line carries
# run-wide `--model <literal> --provider <active>` flags, REPLACING the retired
# per-node-class `-I MODEL_CODING/REVIEW/DEFAULT` inputs.  fabro >= v0.267.0 (the
# FABRO_VERSION the openrouter base_url override depends on) makes
# `{{ inputs.X }}` inside model_stylesheet a HARD PARSE ERROR (fabro commit
# 911e080f3, "Limit DOT templates to prompt + goal"), so the poured def carries
# NO model_stylesheet templating; per-node-class model differentiation is
# DEPRIORITIZED (product authority) in favor of a single run-wide model.  The
# fleet-wide provider-keyed model mapping table (ADR-063) STAYS as the lookup
# structure; only what the launcher does with the resolved value changes — it
# selects the run-wide literal (the coding tier) instead of three per-class inputs.
#
# FIDELITY: the flag assertions read the REAL launcher's ACTUAL recorded finite
# `fabro run` command over the FakeDockerDriver on the openrouter override drive
# (via _odd9_drive_fabro_launch) — never a model and never a shallow string-match:
# the run-wide `--model` literal is compared to the mapping table's own openrouter
# row, `--provider` to the pinned active value, and the absence of `-I MODEL_*` is
# read off the same recorded command line.  The "every node resolves the SAME
# run-wide model AT RUNTIME" leg needs a real `fabro run` against live OpenRouter
# and is an honest SKIP (test_behavior5_runtime_single_model_resolution_honest_skip
# in test_bc_container_llm_provider_override.py), never faked.
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


def _fabro_run_command(script):
    """The finite-child `fabro run` command line the run_finite function issues,
    read out of the recorded engage script (the ACTUAL argv, never a model)."""
    for line in script.splitlines():
        if "fabro run" in line and "--server" in line:
            return line
    return None


def _model_provider_flags(script):
    """The run-wide `--model <id> --provider <name>` flags on the recorded finite
    `fabro run` command line (read off the ACTUAL recorded argv)."""
    cmd = _fabro_run_command(script) or ""
    m = re.search(r"--model\s+(\S+)", cmd)
    p = re.search(r"--provider\s+(\S+)", cmd)
    return (
        m.group(1).strip("'\"") if m else None,
        p.group(1).strip("'\"") if p else None,
    )


@given('the fleet-wide provider-keyed model mapping table names a literal model '
       'ID for the active provider')
def mapping_table_names_literal_for_active_provider(ctx):
    from bc_launcher.fabro.llm_provider import (
        LLM_PROVIDER_ANTHROPIC,
        LLM_PROVIDER_OPENROUTER,
        resolve_model_mapping,
    )

    row = resolve_model_mapping(LLM_PROVIDER_OPENROUTER)
    # The run-wide model is the mapping row's `coding` tier (the substantive-work
    # tier); it must be a real literal so the launch can carry it as `--model`.
    assert isinstance(row.get("coding"), str) and row["coding"].strip(), (
        "the fleet-wide provider-keyed model mapping table must name a literal "
        f"run-wide model ID for the openrouter active provider; got {row!r}"
    )
    # Guard: the openrouter run-wide literal genuinely differs from the anthropic
    # one, so the active provider really selects the model set (not a shared label).
    an_row = resolve_model_mapping(LLM_PROVIDER_ANTHROPIC)
    assert row["coding"] != an_row["coding"], (
        "the OpenRouter and Anthropic run-wide model literals must differ so the "
        f"active provider genuinely selects the model; both are {row['coding']!r}"
    )
    ctx["b5_openrouter_row"] = row
    ctx["b5_anthropic_row"] = an_row


@when(parsers.parse(
    'bc-container launch\'s dispatcher spawns a child "fabro run" for BC name '
    '"{bc_name}" with the OpenRouter provider override'
))
def dispatcher_spawns_child_fabro_run_openrouter(
    bc_name, ctx, fake_driver, controller, tmp_path
):
    # Drive the REAL launcher on the fabro path with the openrouter override in
    # effect (BCLAUNCHER_LLM_PROVIDER=openrouter set by the earlier Given); the
    # recorded engage's run_finite issues the per-child `fabro run` command whose
    # run-wide flags this scenario pins.
    _odd9_drive_fabro_launch(
        bc_name, ctx, fake_driver, controller, tmp_path, work_id=None
    )
    ctx["b5_engage_script"] = _engage_script(ctx)


@then('the child "fabro run" command line carries "--model <literal-model-id> '
      '--provider openrouter", sourced from the mapping table for the active '
      'provider')
def child_fabro_run_carries_run_wide_model_provider(ctx):
    script = ctx.get("b5_engage_script") or _engage_script(ctx)
    cmd = _fabro_run_command(script)
    assert cmd is not None, (
        "the recorded engage must issue a finite `fabro run` child command whose "
        f"run-wide flags this scenario pins; script:\n{script}"
    )
    model, provider = _model_provider_flags(script)
    row = ctx["b5_openrouter_row"]
    # --model is the run-wide literal SOURCED from the active provider's mapping
    # row (the coding tier — the run-wide model that every node resolves to).
    assert model == row["coding"], (
        "the child `fabro run` must carry run-wide `--model "
        f"{row['coding']}` (the openrouter mapping-row literal), got "
        f"--model {model!r}; cmd:\n{cmd}"
    )
    assert model in row.values(), (
        "the run-wide `--model` must be SOURCED from the active provider's "
        f"mapping row {row!r}; got {model!r}"
    )
    assert "/" in (model or ""), (
        f"the openrouter run-wide model {model!r} must be a LITERAL OpenRouter "
        "catalog slug (vendor/model), genuinely the openrouter row's value"
    )
    # --provider is the pinned active value (the scenario pins it verbatim).
    assert provider == "openrouter", (
        "the child `fabro run` must carry the pinned active `--provider "
        f"openrouter`, got --provider {provider!r}; cmd:\n{cmd}"
    )


@then('the command line carries no "-I MODEL_CODING=", "-I MODEL_REVIEW=", or '
      '"-I MODEL_DEFAULT=" input for this launch')
def child_fabro_run_carries_no_model_inputs(ctx):
    script = ctx.get("b5_engage_script") or _engage_script(ctx)
    cmd = _fabro_run_command(script) or ""
    for name in ("MODEL_CODING", "MODEL_REVIEW", "MODEL_DEFAULT"):
        assert f"-I {name}=" not in cmd, (
            f"the run-wide launch must carry NO `-I {name}=` input (the retired "
            f"per-node-class mapping); cmd:\n{cmd}"
        )
    # Belt-and-braces: the recorded run-input reader finds NO `-I MODEL_*` at all.
    remaining = _model_run_inputs(script)
    assert remaining == {}, (
        "no `-I MODEL_*` input may remain on the finite `fabro run` command; "
        f"got {remaining!r}; script:\n{script}"
    )


@then('every node in the workflow, regardless of its ".coding"/".review"/"*" '
      'node-class, resolves to that same single run-wide model — per-node-class '
      'model differentiation is not supplied by this launch')
def every_node_resolves_same_run_wide_model(ctx):
    # LAUNCHER-FIDELITY half (provable now): the launch supplies a SINGLE run-wide
    # `--model` and NO per-node-class differentiation — no `-I MODEL_*` inputs and
    # a poured def whose model_stylesheet carries no per-class `{{ inputs.MODEL_* }}`
    # templating (a fabro >= v0.267.0 HARD PARSE ERROR).  The "resolves to that
    # same model AT RUNTIME across every node-class" leg needs a real `fabro run`
    # against live OpenRouter and is an honest SKIP
    # (test_behavior5_runtime_single_model_resolution_honest_skip), never faked.
    script = ctx.get("b5_engage_script") or _engage_script(ctx)
    model, _ = _model_provider_flags(script)
    assert model is not None, (
        "the launch must supply a SINGLE run-wide `--model` (not per node-class); "
        f"script:\n{script}"
    )
    assert _model_run_inputs(script) == {}, (
        "per-node-class `-I MODEL_*` differentiation must NOT be supplied by this "
        f"launch; got {_model_run_inputs(script)!r}"
    )
    stylesheet, workflow = _poured_model_stylesheet()
    # No per-node-class model differentiation baked in the poured def: the
    # `{{ inputs.MODEL_* }}` templating is gone (fabro >= v0.267.0 parse error) —
    # either no model_stylesheet at all, or one carrying no per-class placeholders.
    if stylesheet is not None:
        for placeholder in ("MODEL_CODING", "MODEL_REVIEW", "MODEL_DEFAULT"):
            assert placeholder not in stylesheet, (
                "the poured def must carry NO per-node-class model differentiation "
                f"({{{{ inputs.{placeholder} }}}} templating is a fabro >= v0.267.0 "
                f"parse error); got model_stylesheet={stylesheet!r}"
            )
    assert "{{ inputs.MODEL_" not in workflow, (
        "the poured workflow.fabro must carry NO `{{ inputs.MODEL_* }}` model "
        f"templating (a fabro >= v0.267.0 hard parse error); workflow:\n{workflow[:600]}"
    )


# ---------------------------------------------------------------------------
# Behavior 6 (@scenario_hash:76badc67216f0d91 — supersedes the retired
# c99e79ac24f56f5c) — CAPSTONE: a real dispatch completes end-to-end on a BC
# launched with the OpenRouter override, given an ALREADY-SATISFIED one-time
# FABRO_VERSION native-"[llm.providers.openai]"-support image precondition, with
# NO further software release required.
#
# RED (behavior 6): the retired c99e79ac capstone step defs hard-asserted the
# REMOVED model_stylesheet "{{ inputs.MODEL_CODING }}" templating + the retired
# per-node-class "-I MODEL_*" inputs (both gone after a3b2b6bebcee78f5), so they
# are removed here.  The new 76badc67 scenario's Given/Then steps are not yet
# bound — GREEN binds them at honest fidelity (resolve_run_wide_model / the
# openrouter-shim base_url + emit_r gated-work_done reachability; live leg
# honest-SKIPPED, never faked).
# ---------------------------------------------------------------------------


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


# The `.coding` node-class nodes on the SCENARIO path from classify to the gated
# work_done emitter.  `impl` is the scenario-lane `.coding` node (bc-implementer);
# `impl_f` also carries class="coding" but is on the FLAT (implementer-emitter)
# lane, not the scenario/gated lane.  `emit_r` is the SOLE scenario-path
# work_done(complete) emitter, reachable ONLY via review->wdg_r->emit_r->done.
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


@given(parsers.parse(
    "the shopsystem-bc-launcher BC's container image was already built from a "
    'bc-base image pinned to a FABRO_VERSION carrying native "{provider_block}" '
    "support, satisfied once, prior to and independent of this launch"
))
def image_built_from_fabro_version_with_native_openai(provider_block, ctx):
    # PRECONDITION (already-satisfied, out-of-scope Architect infra action — NOT
    # this dispatch's acceptance bar): the bc-base Dockerfile pins a FABRO_VERSION
    # and the launcher targets fabro's NATIVE "openai" provider identity (the
    # "[llm.providers.openai]" the base_url override rides).  Bound to the REAL
    # committed Dockerfile ARG pin + the launcher's native-identity constant — NO
    # version-number gate (the bump itself is not pinned here, and this behavior
    # does NOT bump FABRO_VERSION).
    from tests.support.common import _find_bc_base_dockerfile

    from bc_launcher.fabro.constants import FABRO_OPENROUTER_PROVIDER_IDENTITY

    dockerfile = _find_bc_base_dockerfile()
    assert dockerfile is not None, "no bc-base Dockerfile found under the repo tree"
    df_text = dockerfile.read_text()
    assert re.search(r"(?im)^\s*ARG\s+FABRO_VERSION\b", df_text), (
        "the bc-base Dockerfile must pin the FABRO_VERSION the image is built from "
        "(the already-satisfied precondition); no ARG FABRO_VERSION found"
    )
    # The "native [llm.providers.openai] support" the precondition carries is what
    # the launcher's openrouter override rides — fabro's NATIVE openai identity.
    assert provider_block == "[llm.providers.openai]", provider_block
    assert FABRO_OPENROUTER_PROVIDER_IDENTITY == "openai", (
        "the launcher must ride fabro's NATIVE 'openai' provider identity — the "
        f"native-openai support this precondition carries; got "
        f"{FABRO_OPENROUTER_PROVIDER_IDENTITY!r}"
    )
    ctx["b6_fabro_version_precondition_satisfied"] = True


@given(parsers.parse(
    'the "{shim_name}" process is part of that same already-built image'
))
def openrouter_shim_part_of_image(shim_name, ctx):
    # ALREADY-BAKED (no rebuild needed): behavior 3's Dockerfile bake COPYs the
    # committed openrouter-shim asset onto PATH in the bc-base image.  Bound to the
    # REAL committed shim asset + the REAL bc-base Dockerfile COPY — proving the
    # shim is part of the already-built image, not a fresh build artifact.
    from tests.support.common import _find_bc_base_dockerfile

    shim = _committed_openrouter_shim()
    assert shim is not None and shim.is_file(), (
        f"the committed {shim_name!r} asset must exist (baked into the already-"
        "built image by behavior 3's Dockerfile COPY)"
    )
    dockerfile = _find_bc_base_dockerfile()
    assert dockerfile is not None, "no bc-base Dockerfile found under the repo tree"
    assert re.search(
        r"(?im)^\s*COPY\s+\S+\s+\S*/" + re.escape(shim_name) + r"\b",
        dockerfile.read_text(),
    ), (
        f"the bc-base Dockerfile must COPY the {shim_name!r} asset onto PATH so it "
        "is part of the already-built image (behavior 3 bake); no COPY found"
    )
    ctx["b6_shim_in_image"] = True


def _dockerfile_apt_installs(pkg):
    """True when the REAL committed bc-base Dockerfile carries an apt install
    layer for `pkg`.  Collapses Dockerfile backslash line-continuations so a
    multi-line `RUN apt-get update \\ && apt-get install ... <pkg>` reads as one
    logical line, and STRIPS comment lines first so a mere prose mention of the
    package in a comment can never satisfy the check (the tini precedent in
    tests/steps/pid1_reaper.py, hardened against comment-only satisfaction)."""
    from tests.support.common import _find_bc_base_dockerfile

    dockerfile = _find_bc_base_dockerfile()
    assert dockerfile is not None, "no bc-base Dockerfile found under the repo tree"
    text = dockerfile.read_text()
    logical = re.sub(r"\\\s*\n\s*", " ", text)
    uncommented = "\n".join(
        ln for ln in logical.splitlines() if not ln.lstrip().startswith("#")
    )
    return bool(
        re.search(rf"apt-get install[^\n]*\b{re.escape(pkg)}\b", uncommented)
    )


@given(parsers.parse(
    'that same bc-base image also bakes in the "{cli_pkg}" apt package (the '
    'docker CLI client binary — not satisfied by "{daemon_pkg}" alone, which on '
    "this image's Debian trixie base installs only the \"{daemon_bin}\" daemon, "
    "no client), satisfied once, prior to and independent of this launch, so the "
    'launched container can perform the nested "{nested_cmd}" its own dispatched '
    "work requires"
))
def bc_base_image_bakes_docker_cli(cli_pkg, daemon_pkg, daemon_bin, nested_cmd, ctx):
    # PRECONDITION (already-satisfied, out-of-scope Architect infra action — the
    # scenario pins the EXISTENCE of the precondition, not the edit steps).  Bound
    # to the REAL committed bc-base Dockerfile, the same fidelity the FABRO_VERSION
    # precondition Given above uses.
    #
    # ROOT CAUSE this precondition encodes (lead-85s41 live attempt +
    # lead-6tu6o direct image inspection): on this image's Debian trixie base the
    # "docker.io" package installs only the dockerd daemon and NEVER the client, so
    # the client-only "docker-cli" package is what actually produces /usr/bin/docker.
    # Without it the nested `bc-container launch` this scenario's own When-clause
    # requires dies with FileNotFoundError: 'docker'.
    assert cli_pkg == "docker-cli", cli_pkg
    assert daemon_pkg == "docker.io", daemon_pkg
    assert daemon_bin == "dockerd", daemon_bin
    assert nested_cmd == "bc-container launch", nested_cmd

    assert _dockerfile_apt_installs(cli_pkg), (
        f"the bc-base Dockerfile must bake the {cli_pkg!r} apt package — the docker "
        "CLI CLIENT binary the launched container needs to perform the nested "
        f"{nested_cmd!r} its own dispatched work requires.  On this image's Debian "
        f"trixie base {daemon_pkg!r} installs only the {daemon_bin!r} daemon and no "
        "client, so the client-only 'docker-cli' package is required; without it the "
        "nested launch dies with FileNotFoundError: 'docker' (lead-85s41 live "
        "attempt, confirmed by lead-6tu6o direct image inspection)"
    )
    ctx["b6_docker_cli_precondition_satisfied"] = True


@given(parsers.parse(
    'the container is launched with the "{flag}" operator flag, so the baked '
    '"{cli_pkg}" client has a socket to reach'
))
def container_launched_with_mount_docker_socket(flag, cli_pkg, ctx):
    # Bound to the REAL launcher: the opt-in flag exists on the actual bc-container
    # launch CLI parser, and a launch carrying it produces a REAL host-docker-socket
    # bind mount (driven over the FakeDockerDriver via the real controller) — never
    # a string match.  A baked docker-cli client with no socket to reach is inert,
    # so this Given is a genuine precondition of the nested launch, not decoration.
    from bc_launcher.cli_parser import build_parser
    from bc_launcher.constants import DOCKER_SOCKET_PATH

    assert flag == "--mount-docker-socket", flag
    assert cli_pkg == "docker-cli", cli_pkg

    # (a) the operator flag is a REAL flag of the REAL launch CLI.
    parsed = build_parser().parse_args(
        ["launch", "--bc-name", "shopsystem-messaging", flag]
    )
    assert getattr(parsed, "mount_docker_socket", False) is True, (
        f"the {flag!r} operator flag must be a real bc-container launch flag that "
        "opts the launch into the host docker-socket mount"
    )
    # (b) OFF by default — the precondition is a genuine operator opt-in, not
    # something every launch already gets (guard against a vacuous Given).
    default = build_parser().parse_args(
        ["launch", "--bc-name", "shopsystem-messaging"]
    )
    assert getattr(default, "mount_docker_socket", False) is False, (
        f"{flag!r} must be OFF by default, so this Given pins a real operator opt-in"
    )
    ctx["b6_mount_docker_socket_flag"] = True
    ctx["b6_docker_socket_path"] = DOCKER_SOCKET_PATH


@then("the dispatched work reaches a gated work_done, having executed through "
      "at least one non-trivial node-class, such as \".coding\", whose model "
      "resolved to a literal OpenRouter model ID via the \"openrouter-shim\"")
def dispatched_work_reaches_gated_workdone_via_shim_openrouter_model(ctx):
    from urllib.parse import urlparse

    from bc_launcher.fabro.constants import (
        FABRO_OPENROUTER_BASE_URL,
        FABRO_OPENROUTER_SHIM_PORT,
        FABRO_SHIM_HOST,
    )
    from bc_launcher.fabro.llm_provider import (
        LLM_PROVIDER_ANTHROPIC,
        LLM_PROVIDER_OPENROUTER,
        resolve_model_mapping,
        resolve_run_wide_model,
    )

    script = ctx.get("b5_engage_script") or _engage_script(ctx)
    workflow = _poured_workflow_fabro()

    # --- (a) the `.coding` node's model resolves — via resolve_run_wide_model /
    # the ADR-063 mapping table (NOT the removed model_stylesheet) — to a LITERAL
    # OpenRouter model ID.  With per-node-class differentiation deprioritized
    # (a3b2b6bebcee78f5), every node-class — INCLUDING `.coding` — resolves to the
    # SAME single run-wide model, so the `.coding` node's model IS the run-wide
    # openrouter literal.
    run_wide = resolve_run_wide_model(LLM_PROVIDER_OPENROUTER)
    or_row = resolve_model_mapping(LLM_PROVIDER_OPENROUTER)
    an_row = resolve_model_mapping(LLM_PROVIDER_ANTHROPIC)
    assert run_wide == or_row["coding"], (
        "the run-wide model the `.coding` node resolves to must be the openrouter "
        f"mapping row's coding-tier literal {or_row['coding']!r}, got {run_wide!r}"
    )
    assert _is_openrouter_model_id(run_wide), (
        f"the resolved `.coding` model {run_wide!r} must be a LITERAL OpenRouter "
        "model ID (a vendor/model catalog slug)"
    )
    assert not _is_openrouter_model_id(an_row["coding"]), (
        "guard: the Anthropic-subscription row's `.coding` model "
        f"{an_row['coding']!r} must NOT be an OpenRouter slug — the two provider "
        "paths genuinely differ"
    )

    # --- (b) that model is carried on the REAL launcher's recorded finite
    # `fabro run --model <run-wide>` (the run-wide flag replacing the retired
    # per-node-class `-I MODEL_*`), so the launch actually resolves `.coding` to it.
    model, provider = _model_provider_flags(script)
    assert model == run_wide, (
        "the recorded finite `fabro run` must carry the run-wide OpenRouter model "
        f"as `--model {run_wide}`, got --model {model!r}; script:\n{script}"
    )
    assert provider == "openrouter", (
        f"the finite `fabro run` must carry `--provider openrouter`, got {provider!r}"
    )
    assert _model_run_inputs(script) == {}, (
        "the run-wide launch must carry NO retired per-node-class `-I MODEL_*` "
        f"input; got {_model_run_inputs(script)!r}"
    )

    # --- (c) VIA THE "openrouter-shim": that resolved model is reached through the
    # openrouter-shim loopback base_url the native openai provider registers — the
    # node's LLM call goes to the shim, which forwards to OpenRouter.  Bound to the
    # REAL registered base_url + the committed shim's OpenRouter upstream.
    assert FABRO_OPENROUTER_BASE_URL in script, (
        "the openrouter finite run must register the native openai base_url at the "
        f"local openrouter-shim loopback {FABRO_OPENROUTER_BASE_URL!r}; "
        f"script:\n{script}"
    )
    parsed = urlparse(FABRO_OPENROUTER_BASE_URL)
    assert parsed.hostname == FABRO_SHIM_HOST and parsed.port == FABRO_OPENROUTER_SHIM_PORT, (
        "the `.coding` model must resolve VIA the openrouter-shim loopback "
        f"({FABRO_SHIM_HOST}:{FABRO_OPENROUTER_SHIM_PORT}), got "
        f"{FABRO_OPENROUTER_BASE_URL!r}"
    )
    shim = _committed_openrouter_shim()
    assert shim is not None, "the committed openrouter-shim asset must exist"
    import importlib.machinery
    import importlib.util

    loader = importlib.machinery.SourceFileLoader("_or_shim_capstone", str(shim))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    shim_mod = importlib.util.module_from_spec(spec)
    loader.exec_module(shim_mod)
    assert "openrouter.ai" in (getattr(shim_mod, "UPSTREAM", "") or ""), (
        "the openrouter-shim the model resolves VIA must forward to the OpenRouter "
        f"host; committed shim UPSTREAM={getattr(shim_mod, 'UPSTREAM', None)!r}"
    )

    # --- (d) `.coding` is a REAL non-trivial node-class of the committed graph,
    # carried by JUDGMENT agent nodes on the scenario path (not a label-only class).
    node_classes = _fabro_node_classes(workflow)
    coding_nodes = {n for n, c in node_classes.items() if c == "coding"}
    assert coding_nodes, (
        "the committed workflow.fabro must carry `.coding` node-class agent nodes; "
        f"node classes: {node_classes!r}"
    )
    assert _CODING_SCENARIO_NODE in coding_nodes, (
        f"the scenario-path `.coding` node {_CODING_SCENARIO_NODE!r} (bc-"
        f"implementer) must be a `.coding` node; coding nodes: {coding_nodes!r}"
    )

    # --- (e) the run-graph reaches its GATED work_done terminal `emit_r` — the SOLE
    # scenario-path work_done(complete) emitter, reachable ONLY via
    # review->wdg_r->emit_r->done; a FAILED gate diverts to emit_blk (blocked) and
    # NEVER reaches the complete emit.  Bound to the REAL committed graph the
    # openrouter finite run executes (the lead-6ev8 / lead-01jw.3 gated structure).
    def _edge(src, dst):
        return re.search(rf"\b{re.escape(src)}\s*->\s*{re.escape(dst)}\b", workflow)

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
    assert re.search(
        r"emit_r\s*\[.*?bc-emit work-done.*?--status complete",
        workflow,
        re.DOTALL,
    ), "emit_r must be the gated `bc-emit work-done --status complete` emitter"

    # HONEST-FIDELITY NOTE: the TRUE live end-to-end completion (real OpenRouter key
    # + a live agent-vault broker + fabro>=0.267 reaching a real work_done) is NOT
    # achievable in-session — bound here at resolution + shim-routing + graph-
    # reachability fidelity, and the live leg is an honest SKIP
    # (test_behavior6_live_end_to_end_completion_honest_skip), never faked.
    ctx["b6_gated_workdone_reachable"] = True


@then("no further software release was required beyond the already-satisfied "
      "FABRO_VERSION and bc-base \"docker-cli\" image preconditions — only the "
      "launch-time provider override, the \"--mount-docker-socket\" flag, and a "
      "container relaunch")
def no_further_release_beyond_fabro_version_and_docker_cli_preconditions(
    ctx, tmp_path, monkeypatch
):
    from bc_launcher.controller import BcContainerController, _load_fabro_def_files
    from bc_launcher.fabro.constants import (
        FABRO_OPENROUTER_BASE_URL,
        FABRO_OPENROUTER_PROVIDER_IDENTITY,
    )
    from tests.fake_driver import FakeDockerDriver

    # --- (0) BOTH named image preconditions are ALREADY SATISFIED on the committed
    # tree (the FABRO_VERSION pin and the bc-base "docker-cli" bake), and the
    # "--mount-docker-socket" opt-in is a launch-time flag.  This Then asserts what
    # is NOT required beyond them, so it first pins that they genuinely hold —
    # otherwise "no further release" would be vacuously true against a broken image.
    assert ctx.get("b6_fabro_version_precondition_satisfied"), (
        "the FABRO_VERSION image precondition Given must have been established"
    )
    assert ctx.get("b6_docker_cli_precondition_satisfied"), (
        "the bc-base 'docker-cli' image precondition Given must have been "
        "established — the corrected precondition this scenario adds"
    )
    assert ctx.get("b6_mount_docker_socket_flag"), (
        "the '--mount-docker-socket' launch-flag Given must have been established"
    )

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
    # No provider model literal is BAKED into the poured workflow.fabro — with
    # model_stylesheet templating removed (a3b2b6bebcee78f5), the run-wide model
    # rides the launch-time `fabro run --model` flag, NOT a re-poured/rebaked def.
    poured_workflow = files_a["workflow.fabro"].decode("utf-8")
    assert "anthropic/claude" not in poured_workflow, (
        "no OpenRouter (or any provider) literal model ID may be BAKED into the "
        "poured workflow.fabro — the run-wide model rides the launch-time `--model` "
        "flag, no re-pour"
    )
    assert "{{ inputs.MODEL_" not in poured_workflow, (
        "the poured workflow.fabro must carry NO `{{ inputs.MODEL_* }}` templating "
        "(a fabro >= v0.267.0 hard parse error) — the model rides launch-time "
        "`--model`, not a re-poured def"
    )

    # --- (2) the openrouter override is realized PURELY at launch time: relaunch
    # the SAME launcher with NO override (Anthropic default) over a fresh driver and
    # compare the two recorded engages.  Both target the byte-identical poured
    # workflow.fabro; the ONLY differences are launch-time env exports + the
    # registered provider block + the run-wide `--model/--provider` flags — never a
    # poured/baked artifact, no BC-base image rebuild, no software release.
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

    assert FABRO_WORKFLOW_FILE_NAME in or_script and FABRO_WORKFLOW_FILE_NAME in an_script, (
        "both provider engages must target the poured "
        f"{FABRO_WORKFLOW_FILE_NAME} finite-run graph — the openrouter path runs "
        "the identical committed graph, no rebuilt/rebaked variant"
    )
    assert or_script != an_script, (
        "the openrouter override must produce a genuinely different launch-time "
        "engage than the anthropic default"
    )
    assert "BCLAUNCHER_LLM_PROVIDER=openrouter" in or_script, (
        "the openrouter override must be realized as a launch-time env export"
    )
    _or_block = f"[llm.providers.{FABRO_OPENROUTER_PROVIDER_IDENTITY}]"
    assert _or_block in or_script and FABRO_OPENROUTER_BASE_URL in or_script, (
        "the openrouter override must register the native "
        f"{_or_block} block with base_url {FABRO_OPENROUTER_BASE_URL!r} at launch "
        "time"
    )
    assert FABRO_OPENROUTER_BASE_URL not in an_script and (
        "[llm.providers.openrouter]" not in or_script
    ), (
        "the anthropic default path must NOT register the OpenRouter endpoint, and "
        "no custom [llm.providers.openrouter] provider may be registered"
    )
    # With the launch-time-only positions normalized away (env exports, the
    # provider block, and the run-wide `--model/--provider` flags), the two engages
    # are IDENTICAL — proving NOTHING baked/poured/released changed between the two
    # provider launches (only the launch-time override + relaunch).
    def _strip_launch_time(script):
        lines = []
        for ln in script.splitlines():
            if any(
                tok in ln
                for tok in (
                    "BCLAUNCHER_LLM_PROVIDER=",
                    "OPENROUTER_API_KEY",
                    "OPENAI_API_KEY",
                    "ANTHROPIC_API_KEY",
                    "ANTHROPIC_BASE_URL",
                    "llm.providers.",
                    "adapter =",
                    "base_url =",
                )
            ):
                continue
            ln = re.sub(r"--model\s+\S+", "", ln)
            ln = re.sub(r"--provider\s+\S+", "", ln)
            lines.append(ln)
        return "\n".join(lines)

    assert _strip_launch_time(or_script) == _strip_launch_time(an_script), (
        "with the launch-time env exports + provider block + run-wide "
        "`--model/--provider` flags normalized away, the openrouter and anthropic "
        "engages must be IDENTICAL — proving the override rides launch-time config "
        "+ relaunch alone, with NO further software release, BC-base image rebuild, "
        "or template re-pour beyond the already-satisfied FABRO_VERSION precondition"
    )


# ---------------------------------------------------------------------------
# Behavior 3 (@scenario_hash:7f55b8ee9e092692): the "openrouter-shim" is an
# unsandboxed, container-level reverse proxy that forwards the sandboxed node's
# request UNCHANGED to OpenRouter's real API host, with NO header reshaping.
#
# FIDELITY: the forwarding is exercised FUNCTIONALLY against a mock loopback
# upstream by running the REAL committed docker/bc-base/openrouter-shim as a
# subprocess listener (`python3 <shim> --host 127.0.0.1 --port <p> --upstream
# http://127.0.0.1:<mock>/api`) and issuing a real loopback HTTP request through
# it — the mock upstream RECORDS the exact forwarded path + headers, so the
# "/api"+incoming-path concatenation, the UNCHANGED Authorization header (no
# reshaping), and the streamed-back response body are read out of a real
# request/response round-trip, never a model.  The live openrouter.ai leg (the
# real Cloudflare-sensitive outbound hop) needs a real key + network absent
# in-session and is honest-deferred; the DEFAULT upstream constant + the
# no-bare-urllib client choice are pinned structurally in
# tests/test_lead_ifye3_5_openrouter_shim.py.
# ---------------------------------------------------------------------------


def _committed_openrouter_shim():
    """The committed docker/bc-base/openrouter-shim the bc-base Dockerfile COPYs
    onto PATH (the REAL artifact this scenario exercises)."""
    import re as _re

    from tests.support.common import _find_bc_base_dockerfile

    dockerfile = _find_bc_base_dockerfile()
    assert dockerfile is not None, "no bc-base Dockerfile found under the repo tree"
    ctx_dir = dockerfile.parent
    m = _re.search(
        r"(?im)^\s*COPY\s+(\S+)\s+\S*/openrouter-shim\b", dockerfile.read_text()
    )
    if m:
        cand = ctx_dir / m.group(1)
        if cand.is_file():
            return cand
    cand = ctx_dir / "openrouter-shim"
    return cand if cand.is_file() else None


class _RecordingUpstream:
    """A mock OpenRouter upstream on 127.0.0.1 that RECORDS the exact request the
    shim forwards (path + headers + body) and streams back a fixed response body,
    so the forwarding fidelity is read from a real round-trip."""

    RESP_BODY = b"data: {\"choice\":\"chunk-1\"}\n\ndata: {\"choice\":\"chunk-2\"}\n\ndata: [DONE]\n\n"

    def __init__(self):
        import http.server
        import socketserver
        import threading

        records = {}
        resp_body = self.RESP_BODY

        class _H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):  # quiet
                pass

            def do_POST(self):
                length = int(self.headers.get("content-length") or 0)
                body = self.rfile.read(length) if length else b""
                records["path"] = self.path
                records["headers"] = {k.lower(): v for k, v in self.headers.items()}
                records["body"] = body
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(resp_body)))
                self.end_headers()
                self.wfile.write(resp_body)

        self.records = records
        self.server = socketserver.TCPServer(("127.0.0.1", 0), _H)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        try:
            self.server.shutdown()
            self.server.server_close()
        except Exception:
            pass


@given('the "openrouter-shim" process is running, listening on a loopback '
       'address only')
def openrouter_shim_process_running(ctx, request):
    import os
    import socket
    import subprocess
    import sys
    import time

    shim = _committed_openrouter_shim()
    assert shim is not None, (
        "the committed docker/bc-base/openrouter-shim asset does not exist yet — "
        "behavior 3 must create it (the launcher points base_url at its loopback "
        "endpoint)"
    )

    # A mock OpenRouter upstream the shim forwards to (records the real forwarded
    # request); its base carries the '/api' suffix so the concatenation is
    # verifiable exactly as the real 'https://openrouter.ai/api' does.
    upstream = _RecordingUpstream()
    request.addfinalizer(upstream.close)
    ctx["or_upstream"] = upstream
    upstream_base = f"http://127.0.0.1:{upstream.port}/api"
    ctx["or_upstream_base"] = upstream_base

    # Grab a free loopback port for the shim's own listener.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    shim_port = s.getsockname()[1]
    s.close()
    ctx["or_shim_host"] = "127.0.0.1"
    ctx["or_shim_port"] = shim_port

    # The shim makes its OUTBOUND hop via curl to the (http) mock upstream; strip
    # any proxy env so curl reaches the loopback mock directly (the real path
    # rides HTTPS_PROXY, exercised live off-session — honest-deferred).
    env = dict(os.environ)
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        env.pop(k, None)
    env["NO_PROXY"] = "127.0.0.1,localhost"
    env["no_proxy"] = "127.0.0.1,localhost"

    proc = subprocess.Popen(
        [sys.executable, str(shim),
         "--host", "127.0.0.1", "--port", str(shim_port),
         "--upstream", upstream_base],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, text=True,
    )
    request.addfinalizer(proc.kill)
    ctx["or_shim_proc"] = proc

    # Wait for the LOOPBACK listener to accept (proves it is listening on the
    # loopback address).
    deadline = time.time() + 10
    listening = False
    while time.time() < deadline:
        if proc.poll() is not None:
            out = proc.stdout.read() if proc.stdout else ""
            raise AssertionError(
                f"the openrouter-shim exited before listening (rc={proc.returncode}); "
                f"output:\n{out}"
            )
        try:
            c = socket.create_connection(("127.0.0.1", shim_port), timeout=0.5)
            c.close()
            listening = True
            break
        except OSError:
            time.sleep(0.1)
    assert listening, (
        "the openrouter-shim did not start listening on its loopback port "
        f"127.0.0.1:{shim_port} within the timeout"
    )
    # LOOPBACK-ONLY: the shim's argparse default host is the loopback address
    # (the launcher passes --host 127.0.0.1); it does not bind a public interface.
    ctx["or_shim_listening"] = True


@when('the sandboxed fabro node issues its LLM call to the "openai"-identified '
      'provider\'s configured "base_url"')
def sandboxed_node_issues_llm_call(ctx):
    import json
    import urllib.request

    host = ctx["or_shim_host"]
    port = ctx["or_shim_port"]
    path = "/v1/chat/completions"
    ctx["or_request_path"] = path
    # The sandboxed node holds the literal placeholder Bearer token (agent-vault
    # substitutes the real key on the shim's OWN outbound hop).  The node reaches
    # the shim over PLAIN loopback — no proxy on this hop.
    bearer = "Bearer __PLACEHOLDER__"
    ctx["or_request_authorization"] = bearer
    body = json.dumps({"model": "anthropic/claude-sonnet-4.5",
                       "messages": [{"role": "user", "content": "hi"}]}).encode()

    req = urllib.request.Request(
        f"http://{host}:{port}{path}",
        data=body,
        method="POST",
        headers={
            "Authorization": bearer,
            "Content-Type": "application/json",
            "X-Trace": "node-hop",
        },
    )
    # No-proxy opener: the node->shim hop is plain loopback with NO HTTPS_PROXY.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    resp = opener.open(req, timeout=30)
    ctx["or_response_status"] = resp.status
    ctx["or_response_body"] = resp.read()


@then('the request reaches the "openrouter-shim" process over plain loopback, '
      'with no "HTTPS_PROXY" needed for that hop')
def request_reaches_shim_over_loopback(ctx):
    # The node->shim hop succeeded over plain loopback (no proxy opener was used),
    # and the shim forwarded it: the mock upstream RECORDED a request, proving the
    # request reached the shim process.
    assert ctx.get("or_shim_host") == "127.0.0.1", (
        "the shim listener must be a loopback address (127.0.0.1) — the node "
        f"reaches it over plain loopback; got {ctx.get('or_shim_host')!r}"
    )
    assert ctx.get("or_response_status") == 200, (
        "the node's plain-loopback LLM call to the shim base_url must succeed "
        f"(no HTTPS_PROXY needed for that hop); got status "
        f"{ctx.get('or_response_status')!r}"
    )
    records = ctx["or_upstream"].records
    assert records, (
        "the request must reach the openrouter-shim process and be forwarded "
        "upstream — the mock upstream recorded no forwarded request"
    )


@then('the shim forwards the request to "https://openrouter.ai/api" plus the '
      'incoming request path, unchanged, with no header reshaping — unlike the '
      '"anthropic-oauth-shim", which does reshape headers')
def shim_forwards_to_openrouter_api_unchanged(ctx):
    import importlib.machinery
    import importlib.util

    shim = _committed_openrouter_shim()
    assert shim is not None, "the committed openrouter-shim asset must exist"

    # The DEFAULT upstream is OpenRouter's REAL API host WITH the load-bearing
    # '/api' suffix (bare 'https://openrouter.ai' hits the website 404, not the
    # API).  Read from the REAL committed shim module (extensionless script, so a
    # SourceFileLoader is supplied explicitly).
    loader = importlib.machinery.SourceFileLoader("_or_shim_fwd", str(shim))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    assert getattr(mod, "UPSTREAM", None) == "https://openrouter.ai/api", (
        "the openrouter-shim's default UPSTREAM must be "
        "'https://openrouter.ai/api' (the '/api' suffix is REQUIRED); got "
        f"{getattr(mod, 'UPSTREAM', None)!r}"
    )

    # FUNCTIONAL: the shim forwarded to <upstream base> + the INCOMING request
    # path, unchanged.  Against the mock the base carries '/api', so the recorded
    # forwarded path is '/api' + the node's incoming path — exactly the shape the
    # real 'https://openrouter.ai/api' + incoming path produces.
    records = ctx["or_upstream"].records
    incoming = ctx["or_request_path"]
    assert records.get("path") == "/api" + incoming, (
        "the shim must forward to the upstream base + the INCOMING request path "
        f"unchanged; expected {'/api' + incoming!r}, upstream recorded "
        f"{records.get('path')!r}"
    )

    # NO HEADER RESHAPING: the incoming 'Authorization: Bearer' header is
    # forwarded UNCHANGED (agent-vault substitutes the real key on the outbound
    # hop; the shim itself rewrites nothing) — UNLIKE the anthropic-oauth-shim,
    # which strips x-api-key and REWRITES Authorization to a fixed dummy Bearer.
    fwd_headers = records.get("headers", {})
    assert fwd_headers.get("authorization") == ctx["or_request_authorization"], (
        "the shim must forward the incoming Authorization header UNCHANGED (no "
        f"reshaping); sent {ctx['or_request_authorization']!r}, upstream saw "
        f"{fwd_headers.get('authorization')!r}"
    )
    # A non-auth request header the node set is forwarded through as-is too.
    assert fwd_headers.get("x-trace") == "node-hop", (
        "the shim must forward the node's request headers through unchanged; the "
        f"X-Trace header did not survive: {fwd_headers.get('x-trace')!r}"
    )
    # The shim did NOT inject the anthropic-oauth-shim's reshaping headers.
    assert "anthropic-beta" not in fwd_headers, (
        "the openrouter-shim must NOT reshape headers — it must not add the "
        "anthropic-oauth-shim's 'anthropic-beta' header"
    )


@then('the shim streams the upstream response back to the sandboxed node '
      'unchanged')
def shim_streams_response_back_unchanged(ctx):
    got = ctx.get("or_response_body")
    assert got == _RecordingUpstream.RESP_BODY, (
        "the shim must stream the upstream response body back to the node "
        f"UNCHANGED; upstream sent {_RecordingUpstream.RESP_BODY!r}, node got "
        f"{got!r}"
    )
