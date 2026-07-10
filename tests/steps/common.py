"""Step definitions: common (mechanically extracted from conftest.py).

Registered globally via the dynamic pytest_plugins glob in tests/conftest.py;
module boundaries are organizational, not semantic.
"""
from __future__ import annotations

from pytest_bdd import given, when, then, parsers
from tests.conftest import _BC_BASE_PINNED_IMAGE, _agent_vault_launch, _find_bc_base_dockerfile, _find_bc_lead_dockerfile, given, parsers, then, when  # noqa: F401


@given("the shopsystem-bc-launcher BC is installed")
def bc_is_installed(fake_driver, controller, ctx, tmp_path):
    """Set up fixtures and a default credential_home with all standard paths present."""
    ctx["driver"] = fake_driver
    ctx["controller"] = controller
    # Provide a default credential_home with all standard credential dirs/files
    # so that tests which don't configure credentials explicitly still pass.
    # Credential-specific Given steps may override individual paths afterward.
    credential_home = tmp_path / "fake_home"
    credential_home.mkdir(parents=True, exist_ok=True)
    (credential_home / ".claude").mkdir(parents=True, exist_ok=True)
    (credential_home / ".config" / "gh").mkdir(parents=True, exist_ok=True)
    gitconfig = credential_home / ".gitconfig"
    if not gitconfig.exists():
        gitconfig.write_text("")
    ctx["credential_home"] = credential_home


@given(parsers.parse('bc-container launch is run with BC name "{bc_name}"'))
def launch_run_as_given(bc_name, ctx, fake_driver, controller, tmp_path):
    """Used in the isolation scenario where launch is part of the setup."""
    # Configure mounts that bc-container launch would produce
    container_name = f"bc-{bc_name}"
    repo_url = f"https://github.com/shopsystem/{bc_name}.git"
    manifest_path = ctx.get("launch_manifest_path")
    if manifest_path is None:
        default_manifest = tmp_path / "bc-manifest.yaml"
        if not default_manifest.exists():
            import yaml as _yaml
            default_manifest.write_text(_yaml.dump({
                "product": "shopsystem product",
                "bcs": [{"name": bc_name, "remote": repo_url, "role": "bc"}]
            }))
        manifest_path = default_manifest
    credential_home = ctx.get("credential_home")
    result = controller.launch(
        bc_name=bc_name,
        repo_url=repo_url,
        shopmsg_dsn=None,
        manifest_path=manifest_path,
        credential_home=credential_home,
    )
    ctx["launch_result"] = result
    ctx["container_name"] = container_name
    ctx["bc_name"] = bc_name


@then("the command exits zero")
def command_exits_zero(ctx):
    result = ctx.get("result") or ctx.get("help_result")
    if hasattr(result, "exit_code"):
        assert result.exit_code == 0, \
            f"Expected exit 0, got {result.exit_code}\nstderr: {result.stderr}"
    elif hasattr(result, "returncode"):
        assert result.returncode == 0, \
            f"Expected exit 0, got {result.returncode}\nstderr: {result.stderr}"
    elif 'manifest_ok' in ctx:
        assert ctx['manifest_ok'] is True, f'Expected manifest_ok True, got {ctx["manifest_ok"]!r}'
    elif 'list_exit_code' in ctx:
        assert ctx['list_exit_code'] == 0, f'Expected list exit 0, got {ctx["list_exit_code"]}'
    elif 'sync_exit_code' in ctx:
        assert ctx['sync_exit_code'] == 0, f'Expected sync exit 0, got {ctx["sync_exit_code"]}'
    else:
        # send/respond structural checks
        exit_code = ctx.get("send_exit_code", ctx.get("respond_exit_code", 0))
        assert exit_code == 0


@then("the command exits non-zero")
def assert_command_exits_nonzero(ctx):
    # Check CommandResult first (covers bc-container launch/stop/etc)
    result = ctx.get("result")
    if result is not None and hasattr(result, "exit_code"):
        assert result.exit_code != 0, (
            f"Expected non-zero exit code, got {result.exit_code}"
        )
        return
    # Manifest-specific bool check
    exit_code = ctx.get("manifest_ok")
    if exit_code is not None:
        # manifest_ok is a bool: False means non-zero exit
        assert not exit_code, (
            f"Expected non-zero exit, but manifest_ok={exit_code!r}"
        )
    else:
        code = ctx.get("list_exit_code", ctx.get("sync_exit_code", ctx.get("result_exit_code")))
        assert code != 0, f"Expected non-zero exit code, got {code!r}"


@when(parsers.parse('bc-container launch is run with BC name "{bc_name}"'))
def when_launch_run_av(bc_name, ctx, controller, fake_driver, tmp_path):
    _agent_vault_launch(ctx, controller, fake_driver, tmp_path, bc_name,
                        broker=ctx.get("agent_vault_broker"))


@given(parsers.parse('the published image "{image}"'))
def given_published_image(ctx, image):
    images = ctx.setdefault("default_user_images", {})
    if "bc-base" in image:
        images["bc-base"] = _find_bc_base_dockerfile()
    elif "bc-lead" in image:
        images["bc-lead"] = _find_bc_lead_dockerfile()
    else:  # pragma: no cover - scenario only names bc-base / bc-lead
        raise AssertionError(f"Unrecognized published image in scenario: {image!r}")


@given(parsers.parse(
    'the container "{container_name}" is running on the pinned bc-base image'))
def given_container_running_on_bc_base(container_name, ctx, fake_driver):
    # Place the (already-launched) container on the pinned bc-base image and
    # seed its in-container PATH with the bc-base baked tool set — gh and
    # agent-vault among them — but NOT docker (bc-base has none by design).
    fake_driver.set_running_on_bc_base_image(
        container_name, _BC_BASE_PINNED_IMAGE
    )
    assert fake_driver.is_running(container_name), (
        f"Expected {container_name!r} to be running on the bc-base image."
    )
    ctx["container_name"] = container_name
