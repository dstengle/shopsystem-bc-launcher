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
    FABRO_SHIM_PORT,
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
# Behavior 3 (@scenario_hash:14290420156c5ee0): the OpenRouter credential rides
# a NEW agent-vault-brokered credential with NO header-reshaping shim, matching
# the GITHUB_TOKEN no-shim pattern (placeholder node-side, broker substitutes on
# the wire) rather than the Anthropic oauth-shim pattern.
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


def _openrouter_api_key_assignments(ctx):
    """Every OPENROUTER_API_KEY=<value> assignment across the launcher's ACTUAL
    recorded launch execs (command tokens + any stdin input), so the assertions
    read the REAL wiring rather than a re-derivation."""
    out = []
    for c in _cadr_exec_calls(ctx):
        blob = " ".join(c.command)
        if getattr(c, "input", None):
            blob += " " + c.input
        for m in re.finditer(r"OPENROUTER_API_KEY=(\S+)", blob):
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
    # placeholder: the engage exports OPENROUTER_API_KEY so the finite `fabro
    # run` children (provider=local, inheriting the engage env) carry it, and
    # its value is the literal placeholder — the agent-vault broker substitutes
    # the real key on the wire (mirrors GITHUB_TOKEN).
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
    'key onto the outbound "{header}" header only on the wire'
))
def openrouter_mitm_substitutes_bearer_on_wire(header, ctx):
    from bc_launcher.fabro.constants import (
        FABRO_OPENROUTER_ADAPTER,
        FABRO_OPENROUTER_BASE_URL,
    )

    script = _engage_script(ctx)

    # The launcher wiring that MAKES the on-the-wire substitution possible: the
    # openrouter provider is REGISTERED at the server pointing at OpenRouter's
    # REAL API — the OpenAI-compatible endpoint that authenticates via
    # `Authorization: Bearer <key>`.  So the finite run's LLM request is sent
    # with the placeholder Bearer token and forwarded through HTTPS_PROXY, where
    # the agent-vault broker's MITM proxy substitutes the REAL OpenRouter key
    # onto the `Authorization: Bearer` header on the wire (mirrors GITHUB_TOKEN;
    # NOT the anthropic-oauth-shim header-reshaping path).
    assert "[llm.providers.openrouter]" in script, (
        "the openrouter provider must be REGISTERED at the server "
        "([llm.providers.openrouter]) so the finite run's request rides "
        f"HTTPS_PROXY to the broker for wire substitution; script:\n{script}"
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


@then(
    "the real OpenRouter API key is not present in the container's filesystem "
    "or process environment"
)
def openrouter_real_key_absent(ctx):
    # Across EVERY recorded launch exec, the ONLY value OPENROUTER_API_KEY is
    # ever assigned is the literal placeholder — the real key lives ONLY at the
    # broker and rides the wire, never the container fs/env.
    assignments = _openrouter_api_key_assignments(ctx)
    assert assignments, (
        "expected at least one OPENROUTER_API_KEY assignment in the recorded "
        "launch wiring on the openrouter path"
    )
    for call, value in assignments:
        assert value == "__PLACEHOLDER__", (
            "every OPENROUTER_API_KEY in the recorded launch wiring must be the "
            f"literal placeholder, got {value!r} in exec {call.command[:3]!r}"
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
