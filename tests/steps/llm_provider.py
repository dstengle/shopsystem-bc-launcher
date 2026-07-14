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

from tests.support.container import (  # noqa: F401
    _cadr_exec_calls,
    _cadr_fabro_engage_call,
    _odd9_drive_fabro_launch,
)


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
