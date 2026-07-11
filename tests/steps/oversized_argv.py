"""
Step definitions for the oversized-content-blob argv-escape scenarios
(lead-m4zt).

Both placement sites are driven through the REAL launcher against the
FakeDockerDriver with the kernel's MAX_ARG_STRLEN per-argument limit ARMED, so
a launcher that carries a >128 KiB blob as one argv element fails the spawn
with E2BIG exactly as the real docker exec/run does; a launcher that streams
the blob off the argv (docker cp / STDIN) does not.
"""
from __future__ import annotations

from pytest_bdd import given, then, when

from tests.fake_driver import MAX_ARG_STRLEN
from tests.support.container import (
    _cadr_fabro_engage_call,
    _cadr_write_manifest,
    _CADR_LAUNCH_PATH_FABRO,
)

# Comfortably larger than the 128 KiB per-argument limit so, carried on argv,
# it is guaranteed to trip E2BIG; carried on STDIN it is irrelevant.
_OVERSIZED_BYTES = 200 * 1024
_BC_NAME = "shopsystem-messaging"
_DSN = "postgresql://bc@messaging-db:5432/shopmsg"


def _oversized_text() -> str:
    return "B" * _OVERSIZED_BYTES


def _assert_no_argv_element_over_limit(fake_driver):
    """No single argv element of any recorded exec/run carries a >128 KiB blob.

    This is the mechanism teeth: the blob must have LEFT the argv entirely (it
    rides STDIN instead).  Independent of whether the kernel-limit model raised
    — it also catches a blob that happens to sit just under the model's raise
    threshold but is still argv-carried.
    """
    for c in fake_driver.exec_calls:
        for elem in c.command:
            assert len(str(elem).encode("utf-8", "surrogatepass")) <= MAX_ARG_STRLEN, (
                "an oversized content blob is still carried as a single argv "
                f"element: {c.command[:2]!r} (len={len(str(elem).encode())})"
            )


@given(
    "the docker exec boundary enforces the 128 KiB Linux per-single-argument "
    "limit"
)
def arm_arg_limit(fake_driver):
    fake_driver.enforce_argv_strlen_limit(True)


@when(
    "bc-container launch injects a startup prompt larger than 128 KiB on the "
    "tmux engage path"
)
def when_launch_oversized_prompt(ctx, fake_driver, controller, tmp_path):
    container = f"bc-{_BC_NAME}"
    ctx["container_name"] = container
    fake_driver.set_dsn_reachable(_DSN, reachable=True)
    manifest = _cadr_write_manifest(tmp_path, _BC_NAME)
    prompt = _oversized_text()
    ctx["oversized_prompt"] = prompt
    try:
        result = controller.launch(
            bc_name=_BC_NAME,
            repo_url=f"https://github.com/shopsystem/{_BC_NAME}.git",
            shopmsg_dsn=_DSN,
            startup_prompt=prompt,
            manifest_path=manifest,
            credential_home=ctx.get("credential_home"),
        )
        ctx["launch_result"] = result
        ctx["e2big_error"] = None
    except OSError as exc:  # E2BIG surfaces here exactly as the real spawn does
        ctx["launch_result"] = None
        ctx["e2big_error"] = exc


@when(
    "bc-container launch places the fabro def-bundle on the fabro orchestrator "
    "engage path"
)
def when_launch_fabro_bundle(ctx, fake_driver, controller, tmp_path):
    container = f"bc-{_BC_NAME}"
    ctx["container_name"] = container
    manifest = _cadr_write_manifest(tmp_path, _BC_NAME)
    try:
        result = controller.launch(
            bc_name=_BC_NAME,
            repo_url=f"https://github.com/shopsystem/{_BC_NAME}.git",
            manifest_path=manifest,
            credential_home=ctx.get("credential_home"),
            launch_path=_CADR_LAUNCH_PATH_FABRO,
        )
        ctx["launch_result"] = result
        ctx["cadr_driver"] = fake_driver
        ctx["e2big_error"] = None
    except OSError as exc:
        ctx["launch_result"] = None
        ctx["cadr_driver"] = fake_driver
        ctx["e2big_error"] = exc


@then(
    'no single docker argument carries the oversized startup prompt, so the '
    'launch raises no E2BIG "Argument list too long" at the exec boundary'
)
def then_no_e2big_prompt(ctx, fake_driver):
    assert ctx.get("e2big_error") is None, (
        "launch raised E2BIG at the docker exec boundary — the oversized "
        f"blob is still carried as a single argv element: {ctx['e2big_error']!r}"
    )
    _assert_no_argv_element_over_limit(fake_driver)


@then(
    'no single docker argument carries the fabro def-bundle blob, so the '
    'launch raises no E2BIG "Argument list too long" at the exec boundary'
)
def then_no_e2big_bundle(ctx, fake_driver):
    assert ctx.get("e2big_error") is None, (
        "launch raised E2BIG at the docker exec boundary — the def-bundle "
        f"blob is still carried as a single argv element: {ctx['e2big_error']!r}"
    )
    _assert_no_argv_element_over_limit(fake_driver)


@then("the oversized startup prompt is committed to the running agent so the BC comes online")
def then_prompt_committed_online(ctx, fake_driver):
    result = ctx.get("launch_result")
    assert result is not None and result.exit_code == 0, (
        f"launch did not come online: {result!r}"
    )
    committed = fake_driver.agent_committed_input(ctx["container_name"])
    assert committed == ctx["oversized_prompt"], (
        "the oversized startup prompt was not committed to the agent loop "
        f"(agent processing={committed!r})"
    )


@then("the fabro orchestrator engage is started so the BC comes online")
def then_fabro_engage_online(ctx, fake_driver):
    result = ctx.get("launch_result")
    assert result is not None and result.exit_code == 0, (
        f"launch did not come online: {result!r}"
    )
    engage = _cadr_fabro_engage_call(ctx)
    assert engage is not None, (
        "no fabro orchestrator engage (fabro server start + fabro run) exec "
        "was issued, so the BC never engaged"
    )
