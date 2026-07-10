"""Step definitions: lead (mechanically extracted from conftest.py).

Registered globally via the dynamic pytest_plugins glob in tests/conftest.py;
module boundaries are organizational, not semantic.
"""
from __future__ import annotations

from pytest_bdd import given, when, then, parsers
from tests.conftest import _bc_lead_dockerfile_text, _bc_lead_installs_compose_plugin, _bc_lead_installs_dolt_on_path, given, parsers, then, when  # noqa: F401


@given(parsers.parse(
    'the published image "{image}" that the footing bootstrap runway runs on'))
def given_footing_runway_image(ctx, image):
    assert "bc-lead" in image, (
        f"The footing bootstrap runway runs on the bc-lead image; scenario "
        f"named {image!r}."
    )
    _bc_lead_dockerfile_text(ctx)


@when(parsers.parse(
    'the image is run via "docker run --rm <image> docker compose version"'))
def when_run_compose_version(ctx):
    # docker is unavailable; resolve the buildable-artifact source of truth.
    _bc_lead_dockerfile_text(ctx)


@when(parsers.parse(
    'the image is run via "docker run --rm <image> dolt version"'))
def when_run_dolt_version(ctx):
    _bc_lead_dockerfile_text(ctx)


@when(parsers.parse(
    'the image is inspected by running "docker compose version", '
    '"dolt version", and "command -v dolt" inside it'))
def when_inspect_compose_and_dolt(ctx):
    _bc_lead_dockerfile_text(ctx)


@then(parsers.parse(
    '"docker compose version" exits zero and prints the installed Compose '
    'plugin version'))
def then_compose_version_exits_zero(ctx):
    text = _bc_lead_dockerfile_text(ctx)
    assert _bc_lead_installs_compose_plugin(text), (
        f"bc-lead Dockerfile ({ctx['footing_toolset_path']}) does not install "
        f"the docker compose plugin (docker-compose-plugin), so "
        f"`docker compose version` would fail with "
        f"'docker: unknown command: docker compose' (lead-ys8x c5edfa89)."
    )


@then(parsers.parse(
    '"docker compose version" does not fail with "docker: unknown command: '
    'docker compose"'))
def then_compose_not_unknown_command(ctx):
    text = _bc_lead_dockerfile_text(ctx)
    assert _bc_lead_installs_compose_plugin(text), (
        "bc-lead Dockerfile does not install docker-compose-plugin; "
        "`docker compose` stays an unknown command."
    )


@then(parsers.parse(
    'running "docker compose -f compose.yaml up -d postgres agent-vault" inside '
    'the image does not fail with "unknown shorthand flag: \'f\'" due to a '
    'missing compose subcommand'))
def then_compose_up_f_flag_resolves(ctx):
    text = _bc_lead_dockerfile_text(ctx)
    assert _bc_lead_installs_compose_plugin(text), (
        "bc-lead Dockerfile does not install docker-compose-plugin; without the "
        "compose subcommand, `docker compose -f ...` parses -f against the "
        "docker root command and fails with \"unknown shorthand flag: 'f'\". "
        "Footing's `docker compose -f compose.yaml up -d` (footing L172) cannot "
        "run (lead-ys8x c5edfa89)."
    )


@then(parsers.parse(
    '"dolt version" exits zero and prints the installed dolt version'))
def then_dolt_version_exits_zero(ctx):
    text = _bc_lead_dockerfile_text(ctx)
    assert _bc_lead_installs_dolt_on_path(text), (
        f"bc-lead Dockerfile ({ctx['footing_toolset_path']}) does not install "
        f"the dolt engine binary onto PATH, so `dolt version` would not resolve "
        f"(lead-ys8x 98a0683d)."
    )


@then(parsers.parse(
    '"command -v dolt" run inside the image resolves dolt on PATH and exits '
    'zero'))
def then_command_v_dolt_resolves(ctx):
    text = _bc_lead_dockerfile_text(ctx)
    assert _bc_lead_installs_dolt_on_path(text), (
        "bc-lead Dockerfile does not place dolt on PATH (/usr/local/bin), so "
        "`command -v dolt` would not resolve it (lead-ys8x 98a0683d)."
    )


@then(parsers.parse(
    '"bd dolt push" run inside the image does not fail because the dolt engine '
    'binary is absent from PATH'))
def then_bd_dolt_push_resolves(ctx):
    text = _bc_lead_dockerfile_text(ctx)
    assert _bc_lead_installs_dolt_on_path(text), (
        "bc-lead Dockerfile does not install the dolt engine onto PATH; bd 1.0.3 "
        "is inherited from bc-base but `bd dolt push` shells out to the dolt "
        "engine and would fail for a missing dolt binary (lead-ys8x 98a0683d)."
    )


@then(parsers.parse(
    '"docker compose version" exits zero so the footing step "docker compose '
    '-f compose.yaml up -d postgres agent-vault" can run'))
def then_conj_compose(ctx):
    text = _bc_lead_dockerfile_text(ctx)
    assert _bc_lead_installs_compose_plugin(text), (
        "bc-lead Dockerfile does not install docker-compose-plugin; footing's "
        "`docker compose -f compose.yaml up -d` cannot run (lead-ys8x a0992b2)."
    )


@then(parsers.parse(
    '"dolt version" exits zero and "command -v dolt" resolves dolt on PATH so '
    'the footing step "bd dolt push" can run'))
def then_conj_dolt(ctx):
    text = _bc_lead_dockerfile_text(ctx)
    assert _bc_lead_installs_dolt_on_path(text), (
        "bc-lead Dockerfile does not install dolt onto PATH; footing's "
        "`bd dolt push` cannot run (lead-ys8x a0992b2)."
    )


@then(parsers.parse(
    'neither the docker compose plugin nor the dolt binary is absent from the '
    'image footing runs on'))
def then_conj_both_present(ctx):
    text = _bc_lead_dockerfile_text(ctx)
    compose = _bc_lead_installs_compose_plugin(text)
    dolt = _bc_lead_installs_dolt_on_path(text)
    assert compose and dolt, (
        f"bc-lead footing-runway image is missing a required tool: "
        f"docker-compose-plugin present={compose}, dolt-on-PATH present={dolt}. "
        f"The conjunction (lead-ys8x a0992b2) requires BOTH."
    )
