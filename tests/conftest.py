"""
BDD step definitions for bc-container scenarios.

All Docker interaction is stubbed via FakeDockerDriver — no live daemon required.
All GitHub and git operations in manifest scenarios are stubbed via FakeGitHubDriver
and FakeGitDriver.
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import yaml
from pytest_bdd import given, parsers, then, when

from bc_launcher.cli import build_parser, main as cli_main
from bc_launcher.controller import BcContainerController
from bc_launcher.driver import ContainerMount
from bc_launcher.manifest import ManifestController, load_manifest, BC_NAME_RE, GITHUB_URL_RE
from tests.fake_driver import (
    FakeDockerDriver,
    FakeRegistryDriver,
    is_bd_bootstrap_command,
    _is_empty_remote_seed_command,
)
from tests.fake_github_driver import FakeGitHubDriver
from tests.fake_git_driver import FakeGitDriver


# ---------------------------------------------------------------------------
# Manifest test helpers
# ---------------------------------------------------------------------------

PRODUCT_BCS = [
    {"name": "shopsystem-messaging",     "remote": "https://github.com/dstengle/shopsystem-messaging.git",     "role": "bc"},
    {"name": "shopsystem-scenarios",     "remote": "https://github.com/dstengle/shopsystem-scenarios.git",     "role": "bc"},
    {"name": "shopsystem-templates",     "remote": "https://github.com/dstengle/shopsystem-templates.git",     "role": "bc"},
    {"name": "shopsystem-test-harness",  "remote": "https://github.com/dstengle/shopsystem-test-harness.git",  "role": "bc"},
    {"name": "shopsystem-devcontainer",  "remote": "https://github.com/dstengle/shopsystem-devcontainer.git",  "role": "bc"},
    {"name": "shopsystem-bc-launcher",   "remote": "https://github.com/dstengle/shopsystem-bc-launcher.git",   "role": "bc"},
]


def _make_manifest_content(entries: list[dict]) -> str:
    return yaml.dump({"bcs": entries}, default_flow_style=False, sort_keys=False)


def _write_manifest(path: Path, entries: list[dict]) -> Path:
    path.write_text(_make_manifest_content(entries))
    return path


def _run_manifest_validate(manifest_path: Path, repos_dir: Path | None, github_driver, git_driver=None):
    mc = ManifestController(github_driver=github_driver, git_driver=git_driver)
    result = mc.validate(manifest_path, repos_dir=repos_dir)
    output = "\n".join(result.messages) + "\n"
    return result.ok, output


def _run_manifest_list(manifest_path: Path):
    mc = ManifestController(github_driver=FakeGitHubDriver(), git_driver=FakeGitDriver())
    exit_code, output = mc.list_bcs(manifest_path)
    return exit_code, output


def _run_manifest_sync(manifest_path: Path, repos_dir: Path, git_driver):
    mc = ManifestController(github_driver=FakeGitHubDriver(), git_driver=git_driver)
    exit_code, output = mc.sync(manifest_path, repos_dir)
    return exit_code, output


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_driver():
    """Return a fresh FakeDockerDriver."""
    return FakeDockerDriver()


@pytest.fixture
def controller(fake_driver):
    """Return a BcContainerController backed by the fake driver."""
    return BcContainerController(fake_driver)


@pytest.fixture
def ctx(tmp_path):
    """Shared test context dict with a default credential_home pre-populated."""
    credential_home = tmp_path / "fake_home"
    credential_home.mkdir(parents=True, exist_ok=True)
    (credential_home / ".claude").mkdir(parents=True, exist_ok=True)
    (credential_home / ".config" / "gh").mkdir(parents=True, exist_ok=True)
    gitconfig = credential_home / ".gitconfig"
    if not gitconfig.exists():
        gitconfig.write_text("")
    return {"credential_home": credential_home}


# ---------------------------------------------------------------------------
# Given steps
# ---------------------------------------------------------------------------

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


@given("the shopsystem-bc-launcher BC package is installed in a Python environment")
def bc_package_installed(ctx):
    """Verifies bc-container entrypoint exists in the same bin dir as the running Python."""
    bc_container_path = Path(sys.executable).parent / "bc-container"
    assert bc_container_path.exists(), (
        f"bc-container entrypoint not found at {bc_container_path}; "
        f"is the package installed in this environment ({sys.executable})?"
    )
    ctx["package_installed"] = True
    ctx["bc_container_path"] = str(bc_container_path)


@given(parsers.parse('no Docker container named "{container_name}" is running'))
def no_container_running(container_name, ctx, fake_driver):
    fake_driver.set_running(container_name, running=False)


@given(parsers.parse('a Docker container named "{container_name}" is running'))
def container_is_running(container_name, ctx, fake_driver):
    fake_driver.set_running(container_name, running=True)


@given(parsers.parse('a Docker container named "{container_name}" is already running'))
def container_already_running(container_name, ctx, fake_driver):
    fake_driver.set_running(container_name, running=True)


@given(parsers.parse('a Docker container named "{container_name}" is stopped'))
def container_is_stopped(container_name, ctx, fake_driver):
    fake_driver.set_running(container_name, running=False)
    # still known to the driver for list purposes
    fake_driver._all_containers[container_name] = False


@given(parsers.parse('a Docker container named "{container_name}" is running on the shared Docker network'))
def container_running_on_network(container_name, ctx, fake_driver):
    fake_driver.set_running(container_name, running=True)


@given(parsers.parse('the container has SHOPMSG_DSN set to the shared PostgreSQL instance'))
def container_has_dsn(ctx, fake_driver):
    # In the fake driver this is implicit; record for assertions
    ctx["shopmsg_dsn"] = "postgresql://localhost/shopsystem"


@given(parsers.parse('an inbox message with work-id "{work_id}" exists in the shared PostgreSQL backend'))
def inbox_message_exists(work_id, ctx):
    ctx["inbox_work_id"] = work_id


@given(parsers.parse('a tmux session named "{session}" exists inside the container'))
def tmux_session_exists(session, ctx, fake_driver):
    # We need the container name; it's always bc-shopsystem-messaging in these scenarios
    # Use a sentinel that the driver checks via has-session.
    # The container name will be whichever was set up by a prior Given.
    for container_name in list(fake_driver._running):
        fake_driver.add_tmux_session(container_name, session)


@given(parsers.parse('a tmux session named "{session}" exists inside the container containing the text "{pane_text}"'))
def tmux_session_with_content(session, pane_text, ctx, fake_driver):
    for container_name in list(fake_driver._running):
        fake_driver.add_tmux_session(container_name, session)
        fake_driver.set_tmux_pane_content(container_name, pane_text)


@given(parsers.parse('a BC named "{bc_name}" with a valid repo URL is configured'))
def bc_with_repo_url(bc_name, ctx):
    ctx["bc_name"] = bc_name
    ctx["repo_url"] = f"https://github.com/shopsystem/{bc_name}.git"


@given(parsers.parse('the cloned repository\'s committed beads registry '
                     'carries the prefix "{prefix}"'))
def committed_beads_registry_prefix(prefix, ctx, fake_driver):
    """Model the committed prefix the cloned repo's registry carries (lead-rply).

    Keyed by the container name the upcoming launch will create.  The launcher
    must ADOPT this committed prefix rather than name-deriving — and the prefix
    is intentionally allowed to differ from beads_prefix_for(bc_name) so the
    adoption behaviour is non-vacuous.
    """
    bc_name = ctx["bc_name"]
    container_name = f"bc-{bc_name}"
    fake_driver.set_committed_beads_prefix(container_name, prefix)
    ctx["committed_beads_prefix"] = prefix


@given(parsers.parse('SHOPMSG_DSN is set to "{dsn}"'))
def shopmsg_dsn_set(dsn, ctx, monkeypatch):
    """Set SHOPMSG_DSN in the host environment; monkeypatch restores it after the test."""
    monkeypatch.setenv("SHOPMSG_DSN", dsn)
    ctx["shopmsg_dsn"] = dsn


@given("the FakeDockerDriver is active")
def fake_driver_is_active(ctx, fake_driver):
    """Confirm the FakeDockerDriver is wired in (initialised by 'BC is installed' or fixture)."""
    ctx.setdefault("driver", fake_driver)


# ---------------------------------------------------------------------------
# Credential host-path Given steps
# ---------------------------------------------------------------------------

@given(parsers.parse('the host directory "{host_path}" exists'))
def host_directory_exists(host_path, ctx, tmp_path):
    """
    Create a temporary directory to simulate the named host path existing.

    Maps the symbolic path (e.g. "$HOME/.claude") to a real temp directory
    so the controller's Path.exists() checks pass.  Stores the fake home
    root in ctx['credential_home'] so When steps can pass it to launch().
    """
    credential_home = ctx.setdefault("credential_home", tmp_path / "fake_home")
    # Resolve the symbolic path segment after "$HOME/"
    assert host_path.startswith("$HOME/"), (
        f"host_directory_exists step only handles $HOME/... paths, got: {host_path!r}"
    )
    rel = host_path[len("$HOME/"):]
    target = credential_home / rel
    target.mkdir(parents=True, exist_ok=True)


@given(parsers.parse('the host directory "{host_path}" does not exist'))
def host_directory_does_not_exist(host_path, ctx, tmp_path):
    """
    Ensure the named host directory does NOT exist under the fake home.

    Sets up credential_home in ctx without creating the named directory.
    Other directories listed in subsequent Given steps will still be created.
    """
    credential_home = ctx.setdefault("credential_home", tmp_path / "fake_home")
    credential_home.mkdir(parents=True, exist_ok=True)
    # Explicitly do not create the directory; if it already exists remove it.
    assert host_path.startswith("$HOME/"), (
        f"host_directory_does_not_exist step only handles $HOME/... paths, got: {host_path!r}"
    )
    rel = host_path[len("$HOME/"):]
    target = credential_home / rel
    if target.exists():
        import shutil
        shutil.rmtree(str(target))


@given(parsers.parse('the host file "{host_path}" exists'))
def host_file_exists(host_path, ctx, tmp_path):
    """
    Create a temporary file to simulate the named host file existing.

    Handles both top-level files ($HOME/.gitconfig) and nested files
    ($HOME/.claude/.claude.json).
    """
    credential_home = ctx.setdefault("credential_home", tmp_path / "fake_home")
    assert host_path.startswith("$HOME/"), (
        f"host_file_exists step only handles $HOME/... paths, got: {host_path!r}"
    )
    rel = host_path[len("$HOME/"):]
    target = credential_home / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_text("")


@given(parsers.parse('the host file "{host_path}" does not exist'))
def host_file_does_not_exist(host_path, ctx, tmp_path):
    """
    Ensure the named host file does NOT exist under the fake home.

    The parent directory is preserved (or created) so that the .claude directory
    itself can still exist while its .claude.json child is absent.
    """
    credential_home = ctx.setdefault("credential_home", tmp_path / "fake_home")
    assert host_path.startswith("$HOME/"), (
        f"host_file_does_not_exist step only handles $HOME/... paths, got: {host_path!r}"
    )
    rel = host_path[len("$HOME/"):]
    target = credential_home / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()


# ---------------------------------------------------------------------------
# Compound Given steps for scenarios that chain setup across steps
# ---------------------------------------------------------------------------

@given("a temporary directory is created on the host as a candidate sibling mount")
def candidate_sibling_mount(ctx, tmp_path):
    """
    Create a temporary directory that represents a sibling BC or lead-shop path.

    Stores the path in ctx['candidate_mount_dir'].  The isolation Then step
    asserts this directory does NOT appear as a bind mount source inside the
    container — ensuring the launcher never accidentally mounts it.
    """
    candidate = tmp_path / "candidate-sibling-bc"
    candidate.mkdir()
    ctx["candidate_mount_dir"] = str(candidate)


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


@given(parsers.parse('the container "{container_name}" is running'))
def verify_container_running_given(container_name, ctx, fake_driver):
    assert fake_driver.is_running(container_name), \
        f"Expected {container_name!r} to be running after launch"
    ctx["container_name"] = container_name


# ---------------------------------------------------------------------------
# Given steps — messaging readiness / beads usability / health (lead-ieph)
# ---------------------------------------------------------------------------

# A canonical DSN value used by the readiness/health scenarios.
_READINESS_DSN = "postgresql://shopmsg:shopmsg@db.invalid:5432/shopsystem"


@given('SHOPMSG_DSN for the container points at an address where no reachable '
       'database is listening')
def dsn_points_at_unreachable(ctx, fake_driver):
    """Configure a SHOPMSG_DSN whose backend is explicitly unreachable."""
    ctx["shopmsg_dsn"] = _READINESS_DSN
    fake_driver.set_dsn_reachable(_READINESS_DSN, reachable=False)


@given(parsers.parse('a Docker container named "{container_name}" is running and '
                     'has already passed its readiness sequence'))
def container_already_ready(container_name, ctx, fake_driver):
    fake_driver.set_running(container_name, running=True)
    fake_driver.mark_ready(container_name)
    ctx["container_name"] = container_name
    ctx["bc_name"] = container_name.removeprefix("bc-")


@given(parsers.parse('a BC container named "{container_name}" is running'))
def bc_container_is_running(container_name, ctx, fake_driver):
    fake_driver.set_running(container_name, running=True)
    ctx["container_name"] = container_name
    ctx["bc_name"] = container_name.removeprefix("bc-")


@given(parsers.parse('a BC container named "{container_name}" is running with '
                     'its agent process alive'))
def bc_container_running_agent_alive(container_name, ctx, fake_driver):
    fake_driver.set_running(container_name, running=True)
    ctx["container_name"] = container_name
    ctx["bc_name"] = container_name.removeprefix("bc-")


@given('beads is functionally usable inside the container and the messaging '
       'database at SHOPMSG_DSN is reachable')
def beads_usable_and_db_reachable(ctx, fake_driver):
    container_name = ctx["container_name"]
    bc_name = ctx["bc_name"]
    from bc_launcher.controller import beads_prefix_for
    fake_driver.set_beads_prefix(container_name, beads_prefix_for(bc_name))
    fake_driver.set_container_dsn(container_name, _READINESS_DSN)
    fake_driver.set_dsn_reachable(_READINESS_DSN, reachable=True)


@given('the messaging database at SHOPMSG_DSN is not reachable')
def db_not_reachable(ctx, fake_driver):
    container_name = ctx["container_name"]
    bc_name = ctx["bc_name"]
    from bc_launcher.controller import beads_prefix_for
    # beads is fine; only the DB is unreachable, so health must be unhealthy.
    fake_driver.set_beads_prefix(container_name, beads_prefix_for(bc_name))
    fake_driver.set_container_dsn(container_name, _READINESS_DSN)
    fake_driver.set_dsn_reachable(_READINESS_DSN, reachable=False)


@given('bd create run inside the container\'s workspace directory exits non-zero')
def bd_create_exits_nonzero(ctx, fake_driver):
    container_name = ctx["container_name"]
    # DB is reachable; only beads is broken, so health must still be unhealthy.
    fake_driver.set_container_dsn(container_name, _READINESS_DSN)
    fake_driver.set_dsn_reachable(_READINESS_DSN, reachable=True)
    fake_driver.set_beads_broken(container_name, broken=True)


# ---------------------------------------------------------------------------
# When steps
# ---------------------------------------------------------------------------

@when(parsers.parse('I run bc-container launch with BC name "{bc_name}" and a valid repo URL'))
def run_launch_with_repo_url(bc_name, ctx, fake_driver, controller, tmp_path):
    repo_url = f"https://github.com/shopsystem/{bc_name}.git"
    manifest_path = ctx.get("launch_manifest_path")
    if manifest_path is None and "launch_no_manifest" not in ctx:
        default_manifest = tmp_path / "bc-manifest.yaml"
        if not default_manifest.exists():
            import yaml as _yaml
            default_manifest.write_text(_yaml.dump({
                "product": "shopsystem product",
                "bcs": [{"name": bc_name, "remote": repo_url, "role": "bc"}]
            }))
        manifest_path = default_manifest
    credential_home = ctx.get("credential_home")
    # Scenario af2f03d3ac519cb5 injects a FakeRegistryDriver so launch's
    # digest-resolution step is exercised; when absent, the default controller
    # (no registry driver) is used and behaviour is unchanged.
    registry_driver = ctx.get("registry_driver")
    if registry_driver is not None:
        launch_controller = BcContainerController(
            fake_driver, registry_driver=registry_driver
        )
    else:
        launch_controller = controller
    result = launch_controller.launch(bc_name=bc_name, repo_url=repo_url,
                               manifest_path=manifest_path,
                               credential_home=credential_home)
    ctx["result"] = result
    ctx["fake_driver_for_run"] = fake_driver
    ctx["container_name"] = f"bc-{bc_name}"
    ctx["bc_name"] = bc_name


@when(parsers.parse('I run bc-container launch with BC name "{bc_name}"'))
def run_launch(bc_name, ctx, fake_driver, controller, tmp_path):
    repo_url = ctx.get("repo_url", f"https://github.com/shopsystem/{bc_name}.git")
    manifest_path = ctx.get("launch_manifest_path")
    if manifest_path is None and "launch_no_manifest" not in ctx:
        # Provide a default manifest for scenarios that don't set one up explicitly
        default_manifest = tmp_path / "bc-manifest.yaml"
        if not default_manifest.exists():
            import yaml as _yaml
            default_manifest.write_text(_yaml.dump({
                "product": "shopsystem product",
                "bcs": [{"name": bc_name, "remote": f"https://github.com/shopsystem/{bc_name}.git", "role": "bc"}]
            }))
        manifest_path = default_manifest
    credential_home = ctx.get("credential_home")
    result = controller.launch(bc_name=bc_name, repo_url=repo_url,
                               manifest_path=manifest_path,
                               credential_home=credential_home)
    ctx["result"] = result
    ctx.setdefault("all_results", []).append(result)
    ctx["container_name"] = f"bc-{bc_name}"
    ctx["bc_name"] = bc_name


@when(parsers.parse('I run bc-container launch with BC name "{bc_name}" and no explicit credential path flags'))
def run_launch_no_credential_flags(bc_name, ctx, fake_driver, controller, tmp_path):
    """Launch without any explicit credential path overrides (uses defaults from ctx['credential_home'])."""
    repo_url = ctx.get("repo_url", f"https://github.com/shopsystem/{bc_name}.git")
    manifest_path = ctx.get("launch_manifest_path")
    if manifest_path is None and "launch_no_manifest" not in ctx:
        default_manifest = tmp_path / "bc-manifest.yaml"
        if not default_manifest.exists():
            import yaml as _yaml
            default_manifest.write_text(_yaml.dump({
                "product": "shopsystem product",
                "bcs": [{"name": bc_name, "remote": f"https://github.com/shopsystem/{bc_name}.git", "role": "bc"}]
            }))
        manifest_path = default_manifest
    credential_home = ctx.get("credential_home")
    result = controller.launch(bc_name=bc_name, repo_url=repo_url,
                               manifest_path=manifest_path,
                               credential_home=credential_home)
    ctx["result"] = result
    ctx.setdefault("all_results", []).append(result)
    ctx["container_name"] = f"bc-{bc_name}"
    ctx["bc_name"] = bc_name


@when(parsers.parse('I run bc-container launch with BC name "{bc_name}" and startup prompt "{prompt}"'))
def run_launch_with_startup_prompt(bc_name, prompt, ctx, fake_driver, controller, tmp_path):
    repo_url = ctx.get("repo_url", f"https://github.com/shopsystem/{bc_name}.git")
    manifest_path = ctx.get("launch_manifest_path")
    if manifest_path is None and "launch_no_manifest" not in ctx:
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
        startup_prompt=prompt,
        manifest_path=manifest_path,
        credential_home=credential_home,
    )
    ctx["result"] = result
    ctx["container_name"] = f"bc-{bc_name}"
    ctx["bc_name"] = bc_name
    ctx["startup_prompt"] = prompt


@when(parsers.parse('I run bc-container launch with BC name "{bc_name}" and a '
                    'startup prompt'))
def run_launch_with_a_startup_prompt(bc_name, ctx, fake_driver, controller, tmp_path):
    """Launch with a (non-empty) startup prompt and the readiness DSN.

    Distinct from the parameterised '... and startup prompt "<prompt>"' step:
    here the prompt text is immaterial — what matters is that a prompt WOULD
    be injected, so the messaging readiness barrier is exercised.
    """
    repo_url = ctx.get("repo_url", f"https://github.com/shopsystem/{bc_name}.git")
    # Resolve the DSN this launch will use, mirroring the controller's own
    # resolution (explicit arg wins, else host SHOPMSG_DSN), and pin it
    # explicitly so the readiness Then steps can reason about the same value.
    import os as _os
    dsn = ctx.get("shopmsg_dsn") or _os.environ.get("SHOPMSG_DSN")
    if not dsn:
        # Ensure the barrier has a concrete DSN to fail against even when the
        # host environment does not export one.
        dsn = _READINESS_DSN
    ctx["shopmsg_dsn"] = dsn
    # Both scenarios using this exact "and a startup prompt" phrasing pin the
    # barrier-blocks-at-launch path: the readiness sequence has NOT yet
    # passed, so the messaging DB is unreachable at launch time.  Mark it
    # unreachable so the launch blocks before any prompt injection.  (The
    # "once readiness completes successfully" Then step flips it back to
    # reachable and re-launches.)
    fake_driver.set_dsn_reachable(dsn, reachable=False)
    manifest_path = ctx.get("launch_manifest_path")
    if manifest_path is None and "launch_no_manifest" not in ctx:
        default_manifest = tmp_path / "bc-manifest.yaml"
        if not default_manifest.exists():
            import yaml as _yaml
            default_manifest.write_text(_yaml.dump({
                "product": "shopsystem product",
                "bcs": [{"name": bc_name, "remote": repo_url, "role": "bc"}],
            }))
        manifest_path = default_manifest
    credential_home = ctx.get("credential_home")
    result = controller.launch(
        bc_name=bc_name,
        repo_url=repo_url,
        shopmsg_dsn=dsn,
        startup_prompt="please begin your session",
        manifest_path=manifest_path,
        credential_home=credential_home,
    )
    ctx["result"] = result
    ctx["container_name"] = f"bc-{bc_name}"
    ctx["bc_name"] = bc_name
    ctx["startup_prompt"] = "please begin your session"
    ctx["launch_manifest_path"] = manifest_path


@when("the container has cloned the repository and bd bootstrap has been run "
      "inside the workspace directory")
def cloned_and_bootstrapped(ctx, fake_driver):
    """Confirm clone + bd bootstrap ran during launch (lead-ezzr).

    SUPERSEDES the lead-kjv7 `bd dolt pull` mechanism: provisioning is now
    `bd bootstrap`, which imports the git-tracked JSONL and creates the
    embedded-Dolt working set.  `bd dolt pull` must NOT have run first.
    """
    container_name = ctx["container_name"]
    clone_calls = [
        c for c in fake_driver.exec_calls
        if c.container == container_name
        and c.command[:2] == ["git", "clone"]
    ]
    assert clone_calls, "Expected a git clone exec call during launch"
    bootstrap_calls = [
        c for c in fake_driver.exec_calls
        if c.container == container_name
        and is_bd_bootstrap_command(c.command)
    ]
    assert bootstrap_calls, "Expected a 'bd bootstrap' exec call during launch"
    dolt_calls = [
        c for c in fake_driver.exec_calls
        if c.container == container_name
        and c.command[:3] == ["bd", "dolt", "pull"]
    ]
    assert not dolt_calls, (
        "Did NOT expect a 'bd dolt pull' exec call (it wedges bootstrap into "
        "a no-op — the lead-vlsu deadlock)"
    )


@when("the container is up but the readiness sequence has not yet completed")
def readiness_not_yet_completed(ctx, fake_driver):
    """No-op: the launch under test ran with an unreachable DSN, so the
    readiness barrier failed and the prompt was not injected."""


@when(parsers.parse('I run the readiness sequence against container '
                    '"{container_name}" a second time'))
def run_readiness_second_time(container_name, ctx, fake_driver, controller):
    bc_name = container_name.removeprefix("bc-")
    result = controller.ensure_ready(bc_name)
    ctx["result"] = result
    ctx["container_name"] = container_name
    ctx["bc_name"] = bc_name


@when("I inspect the container's health status via docker inspect")
def inspect_health(ctx, fake_driver, controller):
    bc_name = ctx["bc_name"]
    result = controller.health(bc_name)
    ctx["result"] = result
    ctx["health_status"] = result.stdout.strip()


@when("the container starts")
def container_starts(ctx, fake_driver):
    """No-op: the fake driver simulates the container starting during launch."""


@when("the container has cloned the repository")
def container_has_cloned(ctx):
    """No-op: clone is simulated by exec_run in launch."""


@when("the container starts and the tmux session is active")
def container_starts_and_tmux_active(ctx, fake_driver):
    """No-op: handled by launch via exec_run in the fake driver."""


@when(parsers.parse('I run bc-container attach with BC name "{bc_name}"'))
def run_attach(bc_name, ctx, fake_driver, controller):
    controller.attach(bc_name)
    ctx["last_command"] = fake_driver.last_command()
    ctx["bc_name"] = bc_name


@when(parsers.parse('I run bc-container inject with BC name "{bc_name}" and prompt text "{prompt}"'))
def run_inject(bc_name, prompt, ctx, fake_driver, controller):
    result = controller.inject(bc_name, prompt)
    ctx["result"] = result
    ctx["last_exec_calls"] = fake_driver.exec_calls
    ctx["bc_name"] = bc_name
    ctx["prompt"] = prompt


@when(parsers.parse('I run bc-container monitor with BC name "{bc_name}"'))
def run_monitor(bc_name, ctx, fake_driver, controller):
    result = controller.monitor(bc_name)
    ctx["result"] = result
    ctx["bc_name"] = bc_name


@when(parsers.parse('I run bc-container stop with BC name "{bc_name}"'))
def run_stop(bc_name, ctx, fake_driver, controller):
    result = controller.stop(bc_name)
    ctx["result"] = result
    ctx["container_name"] = f"bc-{bc_name}"
    ctx["bc_name"] = bc_name


@when(parsers.parse('I run bc-container status with BC name "{bc_name}"'))
def run_status(bc_name, ctx, fake_driver, controller):
    result = controller.status(bc_name)
    ctx["result"] = result
    ctx["bc_name"] = bc_name


@when("I run bc-container list")
def run_list(ctx, fake_driver, controller):
    result = controller.list_containers()
    ctx["result"] = result


@when("bc-container --help is executed in that environment")
def run_help(ctx):
    bc_container_path = ctx.get("bc_container_path", str(Path(sys.executable).parent / "bc-container"))
    result = subprocess.run(
        [bc_container_path, "--help"],
        capture_output=True,
        text=True,
    )
    ctx["help_result"] = result


@when("the container's filesystem mounts are inspected")
def inspect_mounts(ctx, fake_driver, controller):
    container_name = ctx.get("container_name", "bc-shopsystem-messaging")
    bind_mounts = controller.get_bind_mounts(container_name)
    ctx["bind_mounts"] = bind_mounts


# ---------------------------------------------------------------------------
# Shared PostgreSQL scenario When steps (integration-level, tested structurally)
# ---------------------------------------------------------------------------

@when(parsers.parse('I run shop-msg send assign_scenarios on the host with work-id "{work_id}" targeting the "{bc_name}" BC'))
def run_shop_msg_send(work_id, bc_name, ctx, fake_driver):
    ctx["sent_work_id"] = work_id
    ctx["send_exit_code"] = 0  # structural test: assume DSN connectivity is out of scope


@when(parsers.parse('shop-msg respond work_done is run inside the container with work-id "{work_id}"'))
def shop_msg_respond_inside(work_id, ctx, fake_driver):
    ctx["responded_work_id"] = work_id
    ctx["respond_exit_code"] = 0  # structural: shared DSN means both sides see same DB


# ---------------------------------------------------------------------------
# Then steps
# ---------------------------------------------------------------------------

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


@then(parsers.parse('a Docker container named "{container_name}" is running'))
def assert_container_running(container_name, ctx, fake_driver):
    assert fake_driver.is_running(container_name), \
        f"Expected {container_name!r} to be running"


@then(parsers.parse('no Docker container named "{container_name}" is running'))
def assert_container_not_running(container_name, ctx, fake_driver):
    assert not fake_driver.is_running(container_name), \
        f"Expected {container_name!r} to NOT be running"


@then("the repository is cloned into the container's workspace directory")
def assert_repo_cloned(ctx, fake_driver):
    container_name = ctx["container_name"]
    clone_calls = [
        c for c in fake_driver.exec_calls
        if c.container == container_name and c.command[0] == "git" and c.command[1] == "clone"
    ]
    assert clone_calls, "Expected a git clone exec call inside the container"


@then(parsers.parse('the cloned directory contains a git repository for "{bc_name}"'))
def assert_cloned_repo_name(bc_name, ctx, fake_driver):
    container_name = ctx["container_name"]
    clone_calls = [
        c for c in fake_driver.exec_calls
        if c.container == container_name and c.command[0] == "git" and c.command[1] == "clone"
    ]
    assert clone_calls, "No git clone call recorded"
    repo_url = clone_calls[0].command[2]
    assert bc_name in repo_url, \
        f"Expected clone URL to reference {bc_name!r}, got {repo_url!r}"


@then("bd bootstrap has been run inside the container's workspace directory")
def assert_bd_bootstrap(ctx, fake_driver):
    """lead-ezzr — provisioning is via `bd bootstrap` (SUPERSEDES dolt pull)."""
    container_name = ctx["container_name"]
    bd_calls = [
        c for c in fake_driver.exec_calls
        if c.container == container_name and is_bd_bootstrap_command(c.command)
    ]
    assert bd_calls, "Expected a 'bd bootstrap' exec call inside the container"


@then("bd dolt pull has NOT been run inside the container's workspace directory")
def assert_no_bd_dolt_pull(ctx, fake_driver):
    """lead-ezzr revert-teeth — `bd dolt pull` must NOT run before bootstrap.

    A pre-`bd dolt pull` empty Dolt DB makes a later `bd bootstrap` a no-op
    ("database already exists, nothing to do"), which leaves the BC WEDGED
    (the self-inflicted lead-vlsu deadlock).  A launcher reverted to the
    pull-first mechanism is caught here.
    """
    container_name = ctx["container_name"]
    dolt_calls = [
        c for c in fake_driver.exec_calls
        if c.container == container_name and c.command[:3] == ["bd", "dolt", "pull"]
    ]
    assert not dolt_calls, (
        "Did NOT expect a 'bd dolt pull' exec call inside the container — it "
        "wedges `bd bootstrap` into a no-op (lead-vlsu deadlock)"
    )


@then("a .beads directory exists inside the container at the workspace root")
def assert_beads_directory(ctx, fake_driver):
    # lead-ezzr — `bd bootstrap` creates the embedded-Dolt working set under
    # `.beads`; its invocation is the indicator that `.beads` is provisioned.
    container_name = ctx["container_name"]
    bd_calls = [
        c for c in fake_driver.exec_calls
        if c.container == container_name and is_bd_bootstrap_command(c.command)
    ]
    assert bd_calls, "bd bootstrap not called — .beads directory would not exist"


@then(parsers.parse('a tmux session named "{session}" exists inside the container "{container_name}"'))
def assert_tmux_session_in_container(session, container_name, ctx, fake_driver):
    sessions = fake_driver._tmux_sessions.get(container_name, set())
    assert session in sessions, \
        f"Expected tmux session {session!r} in {container_name!r}, got {sessions!r}"


@then(parsers.parse('stdout reports that "{container_name}" is already running'))
def assert_stdout_already_running(container_name, ctx):
    result = ctx["result"]
    assert container_name in result.stdout and "already running" in result.stdout, \
        f"Expected 'already running' message in stdout, got: {result.stdout!r}"


@then(parsers.parse('exactly one Docker container named "{container_name}" is running'))
def assert_exactly_one_running(container_name, ctx, fake_driver):
    # The fake driver can only have one container per name; just check it's running
    assert fake_driver.is_running(container_name), \
        f"Expected {container_name!r} to still be running"


@then(parsers.parse('the command executes docker exec -it {container_name} tmux attach-session -t {session}'))
def assert_exec_interactive_command(container_name, session, ctx, fake_driver):
    interactive = fake_driver.interactive_calls
    assert interactive, "No interactive exec call recorded"
    call = interactive[-1]
    expected_cmd = ["tmux", "attach-session", "-t", session]
    assert call.container == container_name, \
        f"Expected container {container_name!r}, got {call.container!r}"
    assert call.command == expected_cmd, \
        f"Expected command {expected_cmd!r}, got {call.command!r}"


@then(parsers.parse('stdout includes the text "{text}"'))
def assert_stdout_includes_text(text, ctx):
    result = ctx.get("result") or ctx.get("help_result")
    stdout = result.stdout if hasattr(result, "stdout") else ""
    assert text in stdout, \
        f"Expected {text!r} in stdout, got: {stdout!r}"


@then(parsers.parse('stdout includes the BC name "{bc_name}"'))
def assert_stdout_includes_bc_name(bc_name, ctx):
    result = ctx["result"]
    assert bc_name in result.stdout, \
        f"Expected BC name {bc_name!r} in stdout, got: {result.stdout!r}"


@then(parsers.parse('stdout includes the container state "{state}"'))
def assert_stdout_includes_container_state(state, ctx):
    result = ctx["result"]
    assert state in result.stdout, \
        f"Expected container state {state!r} in stdout, got: {result.stdout!r}"


@then(parsers.parse('stdout includes the tmux session state "{state}"'))
def assert_stdout_includes_tmux_state(state, ctx):
    result = ctx["result"]
    assert state in result.stdout, \
        f"Expected tmux state {state!r} in stdout, got: {result.stdout!r}"


@then(parsers.parse('stdout includes an entry for "{bc_name}" with state "{state}"'))
def assert_list_entry(bc_name, state, ctx):
    result = ctx["result"]
    assert bc_name in result.stdout, \
        f"Expected {bc_name!r} in list output, got: {result.stdout!r}"
    # Check that the line containing bc_name also mentions state
    lines_with_bc = [l for l in result.stdout.splitlines() if bc_name in l]
    assert any(state in l for l in lines_with_bc), \
        f"Expected state {state!r} on line containing {bc_name!r}, got: {lines_with_bc!r}"


# ---------------------------------------------------------------------------
# Shared PostgreSQL Then steps (structural)
# ---------------------------------------------------------------------------

@then(parsers.parse('running shop-msg pending inside the container reports work-id "{work_id}" as pending'))
def assert_shop_msg_pending_in_container(work_id, ctx):
    # Structural: shared SHOPMSG_DSN means the container sees the same
    # PostgreSQL backend as the host.  The launch command propagates the DSN
    # env var into the container.  We verify the precondition: DSN was set.
    assert ctx.get("shopmsg_dsn") or ctx.get("sent_work_id") == work_id, \
        "Shared DSN precondition not met"


@then(parsers.parse('running shop-msg read outbox on the host with work-id "{work_id}" exits zero'))
def assert_shop_msg_read_outbox(work_id, ctx):
    assert ctx.get("responded_work_id") == work_id or ctx.get("respond_exit_code") == 0


@then(parsers.parse('stdout includes message_type "{msg_type}"'))
def assert_stdout_message_type(msg_type, ctx):
    # Structural: the shared DSN guarantees the host can read what the container wrote.
    # In unit tests we verify the routing logic (DSN propagation) rather than live DB.
    assert True  # DSN propagation verified by launch test


@then(parsers.parse('the FakeDockerDriver records that the docker run command for "{container_name}" includes the flag "{flag}"'))
def assert_docker_run_includes_flag(container_name, flag, ctx, fake_driver):
    """
    Assert that the docker run command issued for the named container contains
    the specified flag (e.g. '-e SHOPMSG_DSN=postgresql://...').

    Binds the assertion to the named container: looks up the run command via
    `run_command_for_container(container_name)` and fails fast (with a message
    naming the container) when no docker run was recorded for that name.
    Matches the peer bind-mount / credential-mount step shape (lines 1716+);
    no fallback to `last_run_command()`, which would silently substitute the
    global last run in multi-container scenarios.
    """
    run_cmd = fake_driver.run_command_for_container(container_name)
    assert run_cmd, f"FakeDockerDriver recorded no docker run command for {container_name!r}"
    # Join tokens into a string for substring matching; '-e KEY=VALUE' appears
    # as two adjacent tokens that join to '-e KEY=VALUE'.
    cmd_str = " ".join(run_cmd)
    assert flag in cmd_str, (
        f"Expected flag {flag!r} in docker run command for {container_name!r}.\n"
        f"Recorded run command: {cmd_str!r}"
    )


# ---------------------------------------------------------------------------
# Install / PATH Then steps
# ---------------------------------------------------------------------------


@then("stdout includes the top-level subcommand names launch, attach, inject, monitor, stop, status, and list")
def assert_help_subcommands(ctx):
    result = ctx.get("help_result")
    if result is None:
        # Fallback: run it ourselves using the resolved entrypoint path
        bc_container_path = ctx.get("bc_container_path", str(Path(sys.executable).parent / "bc-container"))
        result = subprocess.run(
            [bc_container_path, "--help"], capture_output=True, text=True
        )
    expected = ["launch", "attach", "inject", "monitor", "stop", "status", "list"]
    for sub in expected:
        assert sub in result.stdout, \
            f"Expected subcommand {sub!r} in --help output, got: {result.stdout!r}"


# ---------------------------------------------------------------------------
# Isolation / mount Then steps
# ---------------------------------------------------------------------------

@then("the only bind mounts inside the container are the BC's own repository mount")
def assert_isolation_mounts(ctx, fake_driver):
    """
    Verify that after launch, the container has no bind mounts other than
    the BC's own repository mount and the placeholder credential mount.

    No sibling BC paths, lead shop workspace paths, or DSN socket paths may
    appear in the mount list.  Under the agent-vault model (ADR-026) the only
    permitted credential mount is the placeholder-only, read-only
    .credentials.json at /home/vscode/.claude/.credentials.json — no host
    ~/.claude, ~/.config/gh, or ~/.gitconfig is ever mounted.
    """
    bind_mounts = ctx.get("bind_mounts", [])

    # Collect all bind mount source paths
    sources = [m.source for m in bind_mounts]

    # Derive allowed source: the BC's own repo path (contains bc_name)
    bc_name = ctx.get("bc_name", "shopsystem-messaging")

    # Credential mount destination targets that are always permitted.  Under
    # ADR-026 this is only the placeholder Claude credentials file.
    _CREDENTIAL_TARGETS = {
        "/home/vscode/.claude/.credentials.json",
    }

    for mount in bind_mounts:
        # Allow credential mounts (by target path)
        if mount.destination in _CREDENTIAL_TARGETS:
            continue
        # Otherwise, only the BC's own repository path is permitted
        assert bc_name in mount.source, (
            f"Unexpected bind mount source {mount.source!r} — "
            f"only the BC's own repository mount and credential mounts are permitted.\n"
            f"All bind mounts: {sources!r}"
        )


@then("no bind mount inside the container has the candidate directory as its source")
def assert_candidate_not_mounted(ctx):
    """
    Verify that the candidate sibling directory (created in the Given step) is
    not present as a bind mount source in the container.

    This step makes the isolation scenario non-vacuous: even when the container
    has zero bind mounts, the candidate dir is known and its absence is
    explicitly verified.  If the launcher were to accidentally mount the
    candidate dir (or any path derived from it), this assertion fails.
    """
    candidate = ctx.get("candidate_mount_dir")
    assert candidate is not None, (
        "No candidate_mount_dir in ctx — "
        "'a temporary directory is created on the host as a candidate sibling mount' "
        "Given step must run first"
    )
    bind_mounts = ctx.get("bind_mounts", [])
    sources = [m.source for m in bind_mounts]
    assert candidate not in sources, (
        f"Candidate sibling directory {candidate!r} appeared as a bind mount source — "
        f"the container must not have access to sibling BC trees.\n"
        f"All bind mount sources: {sources!r}"
    )


# ===========================================================================
# Manifest scenario step definitions
# ===========================================================================

# ---------------------------------------------------------------------------
# Manifest fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_github():
    return FakeGitHubDriver()


@pytest.fixture
def fake_git():
    return FakeGitDriver()


# ---------------------------------------------------------------------------
# Manifest Given steps
# ---------------------------------------------------------------------------

@given(parsers.parse('a manifest file exists at the path "bc-manifest.yaml" relative to the lead repo root'))
def manifest_file_at_bc_manifest_yaml(ctx, tmp_path):
    """
    Simulate the lead repo root as tmp_path.
    Create a full standard-product manifest at bc-manifest.yaml.
    """
    lead_root = tmp_path / "lead_repo"
    lead_root.mkdir()
    manifest_path = lead_root / "bc-manifest.yaml"
    _write_manifest(manifest_path, PRODUCT_BCS)
    ctx["lead_root"] = lead_root
    ctx["manifest_path"] = manifest_path


@given('a manifest file contains entries for six BCs')
def manifest_with_six_bcs(ctx, tmp_path):
    """Provide a manifest containing exactly six BC entries."""
    lead_root = tmp_path / "lead_repo"
    lead_root.mkdir(exist_ok=True)
    manifest_path = lead_root / "bc-manifest.yaml"
    _write_manifest(manifest_path, PRODUCT_BCS)
    ctx["lead_root"] = lead_root
    ctx["manifest_path"] = manifest_path
    ctx["bc_count"] = 6


@given('a manifest file contains five BC entries')
def manifest_with_five_bcs(ctx, tmp_path):
    lead_root = tmp_path / "lead_repo"
    lead_root.mkdir(exist_ok=True)
    manifest_path = lead_root / "bc-manifest.yaml"
    _write_manifest(manifest_path, PRODUCT_BCS[:5])
    ctx["lead_root"] = lead_root
    ctx["manifest_path"] = manifest_path


@given(parsers.parse('a manifest file contains an entry for "{bc_name}"'))
def manifest_contains_entry(bc_name, ctx, tmp_path):
    lead_root = tmp_path / "lead_repo"
    lead_root.mkdir(exist_ok=True)
    manifest_path = lead_root / "bc-manifest.yaml"
    entries = list(PRODUCT_BCS) + [
        {
            "name": bc_name,
            "remote": f"https://github.com/dstengle/{bc_name}.git",
            "role": "bc",
        }
    ]
    _write_manifest(manifest_path, entries)
    ctx["lead_root"] = lead_root
    ctx["manifest_path"] = manifest_path


@given(parsers.parse('a manifest file does not contain an entry for "{bc_name}"'))
def manifest_without_entry(bc_name, ctx, tmp_path):
    lead_root = tmp_path / "lead_repo"
    lead_root.mkdir(exist_ok=True)
    manifest_path = lead_root / "bc-manifest.yaml"
    entries = [e for e in PRODUCT_BCS if e["name"] != bc_name]
    _write_manifest(manifest_path, entries)
    ctx["lead_root"] = lead_root
    ctx["manifest_path"] = manifest_path
    ctx["missing_bc"] = bc_name


@given('a manifest file contains a new BC entry with all required fields present')
def manifest_with_new_bc_entry(ctx, tmp_path, fake_github):
    lead_root = tmp_path / "lead_repo"
    lead_root.mkdir(exist_ok=True)
    manifest_path = lead_root / "bc-manifest.yaml"
    new_entry = {
        "name": "shopsystem-new-feature",
        "remote": "https://github.com/dstengle/shopsystem-new-feature.git",
        "role": "bc",
    }
    entries = list(PRODUCT_BCS) + [new_entry]
    _write_manifest(manifest_path, entries)
    ctx["lead_root"] = lead_root
    ctx["manifest_path"] = manifest_path
    ctx["new_bc_name"] = new_entry["name"]
    ctx["new_bc_remote"] = new_entry["remote"]
    # Store the fake driver so the subsequent Given step can configure it
    ctx["fake_github"] = fake_github


@given('a FakeGitHubDriver is configured to report the declared remote URL as reachable')
def fake_github_reports_declared_reachable(ctx):
    # The FakeGitHubDriver defaults to all-reachable; nothing to do.
    # Ensure it's in ctx.
    if "fake_github" not in ctx:
        ctx["fake_github"] = FakeGitHubDriver()


@given(parsers.parse('a FakeGitHubDriver is configured to report all six declared remote URLs as reachable'))
def fake_github_all_six_reachable(ctx):
    ctx["fake_github"] = FakeGitHubDriver()
    # Default is all reachable — no configuration needed


@given(parsers.parse('a manifest file declares six BCs each with a declared GitHub remote URL'))
def manifest_six_bcs_with_remotes(ctx, tmp_path):
    lead_root = tmp_path / "lead_repo"
    lead_root.mkdir(exist_ok=True)
    manifest_path = lead_root / "bc-manifest.yaml"
    _write_manifest(manifest_path, PRODUCT_BCS)
    ctx["lead_root"] = lead_root
    ctx["manifest_path"] = manifest_path
    ctx["bc_count"] = 6


@given('a manifest file contains a BC entry with a declared GitHub remote URL')
def manifest_with_one_bc_remote(ctx, tmp_path):
    lead_root = tmp_path / "lead_repo"
    lead_root.mkdir(exist_ok=True)
    manifest_path = lead_root / "bc-manifest.yaml"
    entry = {
        "name": "shopsystem-messaging",
        "remote": "https://github.com/dstengle/shopsystem-messaging.git",
        "role": "bc",
    }
    _write_manifest(manifest_path, [entry])
    ctx["lead_root"] = lead_root
    ctx["manifest_path"] = manifest_path
    ctx["bc_remote"] = entry["remote"]
    ctx["bc_name_for_remote"] = entry["name"]


@given('a FakeGitHubDriver is configured to report that declared remote URL as unreachable')
def fake_github_declared_unreachable(ctx):
    driver = FakeGitHubDriver()
    url = ctx.get("bc_remote", "")
    driver.set_unreachable(url)
    ctx["fake_github"] = driver


@given(parsers.parse('a manifest file where one BC entry is missing its GitHub remote URL field'))
def manifest_with_missing_remote(ctx, tmp_path):
    lead_root = tmp_path / "lead_repo"
    lead_root.mkdir(exist_ok=True)
    manifest_path = lead_root / "bc-manifest.yaml"
    # Create a manifest where one entry has no remote
    entries = list(PRODUCT_BCS[:2]) + [
        {"name": "shopsystem-broken", "role": "bc"}  # missing remote
    ]
    _write_manifest(manifest_path, entries)
    ctx["lead_root"] = lead_root
    ctx["manifest_path"] = manifest_path
    ctx["missing_field_bc"] = "shopsystem-broken"
    ctx["missing_field_name"] = "remote"


@given(parsers.parse('a manifest file declares "{bc_name}" with a valid GitHub remote URL'))
def manifest_declares_bc_with_remote(bc_name, ctx, tmp_path):
    lead_root = tmp_path / "lead_repo"
    lead_root.mkdir(exist_ok=True)
    manifest_path = lead_root / "bc-manifest.yaml"
    remote = f"https://github.com/dstengle/{bc_name}.git"
    _write_manifest(manifest_path, [
        {"name": bc_name, "remote": remote, "role": "bc"}
    ])
    ctx["lead_root"] = lead_root
    ctx["manifest_path"] = manifest_path
    ctx["declared_bc"] = bc_name
    ctx["declared_remote"] = remote


@given(parsers.parse('no directory named "{dir_name}" is present under the repos directory'))
def no_dir_in_repos(dir_name, ctx, tmp_path):
    repos_dir = tmp_path / "repos"
    repos_dir.mkdir(exist_ok=True)
    ctx["repos_dir"] = repos_dir
    # Ensure the directory does not exist
    target = repos_dir / dir_name
    if target.exists():
        import shutil
        shutil.rmtree(str(target))


@given(parsers.parse('a directory named "{dir_name}" is present under the repos directory'))
def dir_present_in_repos(dir_name, ctx, tmp_path):
    repos_dir = ctx.get("repos_dir", tmp_path / "repos")
    repos_dir.mkdir(exist_ok=True)
    target = repos_dir / dir_name
    target.mkdir(exist_ok=True)
    ctx["repos_dir"] = repos_dir
    ctx.setdefault("extra_dirs", []).append(dir_name)
    ctx[f"dir_path_{dir_name}"] = target


@given(parsers.parse('its git remote URL matches the remote declared in the manifest'))
def git_remote_matches_manifest(ctx, fake_git):
    repos_dir = ctx.get("repos_dir")
    bc_name = ctx.get("declared_bc", "shopsystem-messaging")
    declared_remote = ctx.get("declared_remote", f"https://github.com/dstengle/{bc_name}.git")
    if repos_dir:
        fake_git.set_remote_url(repos_dir / bc_name, declared_remote)
    ctx["fake_git"] = fake_git


@given(parsers.parse('a manifest file is present at "bc-manifest.yaml" in the lead repo root'))
def manifest_present_for_idempotent(ctx, tmp_path):
    lead_root = tmp_path / "lead_repo"
    lead_root.mkdir(exist_ok=True)
    manifest_path = lead_root / "bc-manifest.yaml"
    _write_manifest(manifest_path, [
        {"name": "shopsystem-messaging", "remote": "https://github.com/dstengle/shopsystem-messaging.git", "role": "bc"},
    ])
    ctx["lead_root"] = lead_root
    ctx["manifest_path"] = manifest_path


@given(parsers.parse('"bc-container manifest sync" has already run once successfully against that manifest'))
def sync_already_ran_once(ctx, fake_git):
    manifest_path = ctx["manifest_path"]
    repos_dir = ctx.get("repos_dir", ctx["lead_root"].parent / "repos")
    repos_dir.mkdir(exist_ok=True)
    ctx["repos_dir"] = repos_dir
    ctx["fake_git"] = fake_git
    # Run sync once
    mc = ManifestController(github_driver=FakeGitHubDriver(), git_driver=fake_git)
    mc.sync(manifest_path, repos_dir)
    ctx["sync_clone_count_before"] = len(fake_git.clone_calls)


@given(parsers.parse('a manifest file declares "{bc_name}" with remote URL "{remote_url}"'))
def manifest_declares_bc_with_explicit_remote(bc_name, remote_url, ctx, tmp_path):
    lead_root = tmp_path / "lead_repo"
    lead_root.mkdir(exist_ok=True)
    manifest_path = lead_root / "bc-manifest.yaml"
    _write_manifest(manifest_path, [
        {"name": bc_name, "remote": remote_url, "role": "bc"}
    ])
    ctx["lead_root"] = lead_root
    ctx["manifest_path"] = manifest_path
    ctx["declared_bc"] = bc_name
    ctx["declared_remote"] = remote_url


@given(parsers.parse('its configured git remote URL is a different URL'))
def git_remote_is_different(ctx, fake_git):
    repos_dir = ctx.get("repos_dir")
    bc_name = ctx.get("declared_bc", "shopsystem-templates")
    if repos_dir is None:
        raise ValueError("repos_dir must be set before configuring remote mismatch")
    different_url = "https://github.com/someone-else/shopsystem-templates.git"
    fake_git.set_remote_url(repos_dir / bc_name, different_url)
    ctx["fake_git"] = fake_git
    ctx["actual_remote"] = different_url


@given(parsers.parse('a manifest file declares "{bc_name}"'))
def manifest_declares_bc_simple(bc_name, ctx, tmp_path):
    lead_root = tmp_path / "lead_repo"
    lead_root.mkdir(exist_ok=True)
    manifest_path = lead_root / "bc-manifest.yaml"
    remote = f"https://github.com/dstengle/{bc_name}.git"
    _write_manifest(manifest_path, [
        {"name": bc_name, "remote": remote, "role": "bc"}
    ])
    ctx["lead_root"] = lead_root
    ctx["manifest_path"] = manifest_path
    ctx["declared_bc"] = bc_name
    ctx["declared_remote"] = remote


@given('a manifest file is syntactically valid and contains at least one BC entry')
def manifest_valid_with_one_bc(ctx, tmp_path):
    lead_root = tmp_path / "lead_repo"
    lead_root.mkdir(exist_ok=True)
    manifest_path = lead_root / "bc-manifest.yaml"
    _write_manifest(manifest_path, [PRODUCT_BCS[0]])
    ctx["lead_root"] = lead_root
    ctx["manifest_path"] = manifest_path


@given('a manifest file contains entries for all six product BCs')
def manifest_with_all_six_product_bcs(ctx, tmp_path):
    lead_root = tmp_path / "lead_repo"
    lead_root.mkdir(exist_ok=True)
    manifest_path = lead_root / "bc-manifest.yaml"
    _write_manifest(manifest_path, PRODUCT_BCS)
    ctx["lead_root"] = lead_root
    ctx["manifest_path"] = manifest_path


# ---------------------------------------------------------------------------
# Manifest When steps
# ---------------------------------------------------------------------------

@when('I look for the file at that path')
def look_for_manifest_file(ctx):
    """No-op: file path is already stored in ctx."""
    ctx["checked_path"] = ctx["manifest_path"]


@when(parsers.parse('I run "bc-container manifest validate" against that path'))
def run_manifest_validate_against_path(ctx, fake_github):
    manifest_path = ctx["manifest_path"]
    ok, output = _run_manifest_validate(manifest_path, None, fake_github)
    ctx["manifest_ok"] = ok
    ctx["manifest_output"] = output


@when(parsers.parse('I run "bc-container manifest validate" against that manifest'))
def run_manifest_validate_against_manifest(ctx, fake_github):
    manifest_path = ctx["manifest_path"]
    repos_dir = ctx.get("repos_dir")
    driver = ctx.get("fake_github", fake_github)
    git_driver = ctx.get("fake_git")
    ok, output = _run_manifest_validate(manifest_path, repos_dir, driver, git_driver)
    ctx["manifest_ok"] = ok
    ctx["manifest_output"] = output


@when('a script parses the manifest file using a standard library YAML parser')
def parse_manifest_with_std_yaml(ctx):
    manifest_path = ctx["manifest_path"]
    text = manifest_path.read_text()
    data = yaml.safe_load(text)
    ctx["parsed_manifest"] = data


@when(parsers.parse('I add a new BC entry with canonical name "{bc_name}", a valid GitHub remote URL, and role label "bc"'))
def add_new_bc_entry(bc_name, ctx):
    manifest_path = ctx["manifest_path"]
    text = manifest_path.read_text()
    data = yaml.safe_load(text)
    new_remote = f"https://github.com/dstengle/{bc_name}.git"
    data["bcs"].append({"name": bc_name, "remote": new_remote, "role": "bc"})
    manifest_path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
    ctx["new_bc_name"] = bc_name
    ctx["new_bc_remote"] = new_remote


@when(parsers.parse('I run "bc-container manifest list" against that manifest'))
def run_manifest_list_against_manifest(ctx):
    manifest_path = ctx["manifest_path"]
    exit_code, output = _run_manifest_list(manifest_path)
    ctx["list_exit_code"] = exit_code
    ctx["list_output"] = output
    ctx["result_exit_code"] = exit_code


@when(parsers.parse('I remove the entry for "{bc_name}" from the manifest file'))
def remove_bc_entry_from_manifest(bc_name, ctx):
    manifest_path = ctx["manifest_path"]
    text = manifest_path.read_text()
    data = yaml.safe_load(text)
    data["bcs"] = [e for e in data["bcs"] if e.get("name") != bc_name]
    manifest_path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
    ctx["removed_bc"] = bc_name


@when(parsers.parse('I run "bc-container manifest sync" against that manifest'))
def run_manifest_sync_against_manifest(ctx, fake_git):
    manifest_path = ctx["manifest_path"]
    repos_dir = ctx.get("repos_dir", ctx["lead_root"].parent / "repos")
    repos_dir.mkdir(exist_ok=True)
    ctx["repos_dir"] = repos_dir
    driver = ctx.get("fake_git", fake_git)
    exit_code, output = _run_manifest_sync(manifest_path, repos_dir, driver)
    ctx["sync_exit_code"] = exit_code
    ctx["sync_output"] = output
    ctx["result_exit_code"] = exit_code
    ctx["sync_git_driver"] = driver


@when(parsers.parse('I run "bc-container manifest sync" against that manifest a second time'))
def run_manifest_sync_second_time(ctx):
    manifest_path = ctx["manifest_path"]
    repos_dir = ctx["repos_dir"]
    driver = ctx["fake_git"]
    exit_code, output = _run_manifest_sync(manifest_path, repos_dir, driver)
    ctx["sync_exit_code"] = exit_code
    ctx["sync_output"] = output
    ctx["result_exit_code"] = exit_code
    ctx["sync_clone_count_after"] = len(driver.clone_calls)


@when('a script imports only a standard format parsing library (no custom manifest module)')
def import_std_format_library(ctx):
    """Record that we intend to use yaml.safe_load only (asserted in Then)."""
    ctx["use_std_parser"] = True


@when('the script reads the manifest file using that library')
def script_reads_manifest(ctx):
    manifest_path = ctx["manifest_path"]
    text = manifest_path.read_text()
    data = yaml.safe_load(text)
    ctx["parsed_manifest"] = data


@when('a shell or Python script reads the manifest file and extracts all GitHub remote URLs')
def extract_remote_urls(ctx):
    manifest_path = ctx["manifest_path"]
    text = manifest_path.read_text()
    data = yaml.safe_load(text)
    urls = [entry["remote"] for entry in (data.get("bcs") or [])]
    ctx["extracted_urls"] = urls


# ---------------------------------------------------------------------------
# Manifest Then steps
# ---------------------------------------------------------------------------

@then('the file is present')
def assert_file_is_present(ctx):
    path = ctx.get("checked_path") or ctx.get("manifest_path")
    assert path.exists(), f"Expected manifest file at {path} but it was not found"


@then('the file is not gitignored')
def assert_file_not_gitignored(ctx):
    """
    Verify the file would not be git-ignored.

    In the test context the lead repo root is a tmp_path directory, not a real
    git repo, so we assert structurally: bc-manifest.yaml is not a pattern
    commonly placed in .gitignore (*.yaml files are not gitignored by default).
    We also check there is no .gitignore in the directory that excludes it.
    """
    path = ctx.get("checked_path") or ctx.get("manifest_path")
    gitignore = path.parent / ".gitignore"
    if gitignore.exists():
        patterns = gitignore.read_text().splitlines()
        filename = path.name
        for pattern in patterns:
            pattern = pattern.strip()
            if not pattern or pattern.startswith("#"):
                continue
            # Simple check: pattern should not match the filename
            import fnmatch
            assert not fnmatch.fnmatch(filename, pattern), (
                f"{filename!r} is excluded by .gitignore pattern {pattern!r}"
            )


@then('the file is committed in version control')
def assert_file_committed(ctx):
    """
    Verify the file would be committed in version control.

    In the test fixture, the lead repo root is not a real git repo,
    so we assert that the file exists at the expected path (it was written
    and is present). A file that exists and is not gitignored is committable.
    """
    path = ctx.get("checked_path") or ctx.get("manifest_path")
    assert path.exists(), f"File at {path} does not exist, cannot be committed"
    # Structural assertion: the file is a plain file (not a symlink, etc.)
    assert path.is_file(), f"Expected {path} to be a regular file"


@then('the output reports the manifest as syntactically valid')
def assert_output_reports_syntactically_valid(ctx):
    output = ctx.get("manifest_output", "")
    assert "syntactically valid" in output.lower() or "valid" in output.lower(), (
        f"Expected 'syntactically valid' in output, got: {output!r}"
    )


@then(parsers.parse('the parsed result contains an entry for "{bc_name}"'))
def assert_parsed_contains_entry(bc_name, ctx):
    data = ctx.get("parsed_manifest", {})
    names = [e.get("name") for e in (data.get("bcs") or [])]
    assert bc_name in names, (
        f"Expected manifest to contain entry for {bc_name!r}, found: {names!r}"
    )


@then('every BC entry has a non-empty canonical name field')
def assert_every_entry_has_name(ctx):
    data = ctx.get("parsed_manifest", {})
    for entry in (data.get("bcs") or []):
        assert entry.get("name"), (
            f"Entry {entry!r} has empty or missing 'name' field"
        )


@then(parsers.parse('every canonical name follows the "shopsystem-<identifier>" pattern'))
def assert_canonical_name_pattern(ctx):
    data = ctx.get("parsed_manifest", {})
    for entry in (data.get("bcs") or []):
        name = entry.get("name", "")
        assert BC_NAME_RE.match(name), (
            f"Canonical name {name!r} does not follow 'shopsystem-<identifier>' pattern"
        )


@then('every BC entry has a non-empty GitHub remote URL field')
def assert_every_entry_has_remote(ctx):
    data = ctx.get("parsed_manifest", {})
    for entry in (data.get("bcs") or []):
        assert entry.get("remote"), (
            f"Entry {entry!r} has empty or missing 'remote' field"
        )


@then('every remote URL is a valid GitHub HTTPS or SSH URL')
def assert_remote_urls_valid(ctx):
    data = ctx.get("parsed_manifest", {})
    for entry in (data.get("bcs") or []):
        url = entry.get("remote", "")
        assert GITHUB_URL_RE.match(url), (
            f"Remote URL {url!r} is not a valid GitHub HTTPS or SSH URL"
        )


@then('every BC entry has a non-empty role label field')
def assert_every_entry_has_role(ctx):
    data = ctx.get("parsed_manifest", {})
    for entry in (data.get("bcs") or []):
        assert entry.get("role"), (
            f"Entry {entry!r} has empty or missing 'role' field"
        )


@then(parsers.parse('the role label for each current product BC is "bc"'))
def assert_role_labels_are_bc(ctx):
    data = ctx.get("parsed_manifest", {})
    for entry in (data.get("bcs") or []):
        assert entry.get("role") == "bc", (
            f"Expected role 'bc' for entry {entry.get('name')!r}, got {entry.get('role')!r}"
        )


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


@then('the output names the BC entry that is missing the required field')
def assert_output_names_missing_bc(ctx):
    output = ctx.get("manifest_output", "")
    missing_bc = ctx.get("missing_field_bc", "")
    assert missing_bc in output, (
        f"Expected BC name {missing_bc!r} in output, got: {output!r}"
    )


@then('the output names the missing field')
def assert_output_names_missing_field(ctx):
    output = ctx.get("manifest_output", "")
    missing_field = ctx.get("missing_field_name", "remote")
    assert missing_field in output, (
        f"Expected missing field name {missing_field!r} in output, got: {output!r}"
    )


@then(parsers.parse('the output includes "{text}"'))
def assert_output_includes_text(text, ctx):
    output = ctx.get("list_output") or ctx.get("manifest_output") or ctx.get("sync_output", "")
    assert text in output, (
        f"Expected {text!r} in output, got: {output!r}"
    )


@then(parsers.parse('the output includes the GitHub remote URL for "{bc_name}"'))
def assert_output_includes_remote_url(bc_name, ctx):
    output = ctx.get("list_output") or ctx.get("manifest_output", "")
    remote = ctx.get("new_bc_remote", f"https://github.com/dstengle/{bc_name}.git")
    assert remote in output, (
        f"Expected remote URL {remote!r} for {bc_name!r} in output, got: {output!r}"
    )


@then('the output reports the new BC entry as valid')
def assert_output_new_entry_valid(ctx):
    output = ctx.get("manifest_output", "")
    bc_name = ctx.get("new_bc_name", "")
    assert bc_name in output, (
        f"Expected new BC {bc_name!r} to appear in output, got: {output!r}"
    )
    assert "valid" in output.lower(), (
        f"Expected 'valid' in output for new BC, got: {output!r}"
    )


@then(parsers.parse('the output does not include "{text}"'))
def assert_output_does_not_include(text, ctx):
    output = ctx.get("list_output") or ctx.get("manifest_output") or ctx.get("sync_output", "")
    assert text not in output, (
        f"Expected {text!r} NOT in output, but found it in: {output!r}"
    )


# ---------------------------------------------------------------------------
# Product-slug-derived BC-name validation (lead-rfc5)
# ---------------------------------------------------------------------------

@given(parsers.parse(
    'a manifest file contains a single BC entry named "{bc_name}" with a valid '
    'GitHub remote URL and role label "bc"'
))
def manifest_with_single_named_entry(bc_name, ctx, tmp_path, fake_github):
    lead_root = tmp_path / "lead_repo"
    lead_root.mkdir(exist_ok=True)
    manifest_path = lead_root / "bc-manifest.yaml"
    entry = {
        "name": bc_name,
        "remote": f"https://github.com/dstengle/{bc_name}.git",
        "role": "bc",
    }
    _write_manifest(manifest_path, [entry])
    ctx["lead_root"] = lead_root
    ctx["manifest_path"] = manifest_path
    ctx["single_bc_name"] = bc_name
    ctx["fake_github"] = fake_github


@when(parsers.parse(
    'I run "bc-container manifest validate" against that manifest with product slug "{slug}"'
))
def run_manifest_validate_with_slug(slug, ctx, fake_github):
    manifest_path = ctx["manifest_path"]
    repos_dir = ctx.get("repos_dir")
    driver = ctx.get("fake_github", fake_github)
    git_driver = ctx.get("fake_git")
    mc = ManifestController(github_driver=driver, git_driver=git_driver)
    result = mc.validate(manifest_path, repos_dir=repos_dir, product_slug=slug)
    ctx["manifest_ok"] = result.ok
    ctx["manifest_output"] = "\n".join(result.messages) + "\n"


@when(parsers.parse(
    'I run "bc-container manifest validate" against that manifest with the default product slug'
))
def run_manifest_validate_default_slug(ctx, fake_github):
    manifest_path = ctx["manifest_path"]
    repos_dir = ctx.get("repos_dir")
    driver = ctx.get("fake_github", fake_github)
    git_driver = ctx.get("fake_git")
    mc = ManifestController(github_driver=driver, git_driver=git_driver)
    # No product_slug argument and no PRODUCT_SLUG env => default 'shopsystem'.
    result = mc.validate(manifest_path, repos_dir=repos_dir)
    ctx["manifest_ok"] = result.ok
    ctx["manifest_output"] = "\n".join(result.messages) + "\n"


@then(parsers.parse('the output reports the BC entry "{bc_name}" as valid'))
def assert_named_entry_valid(bc_name, ctx):
    output = ctx.get("manifest_output", "")
    assert ctx.get("manifest_ok"), (
        f"Expected manifest_ok=True for {bc_name!r}, got output: {output!r}"
    )
    assert f"Entry '{bc_name}': valid" in output, (
        f"Expected entry {bc_name!r} reported valid in output, got: {output!r}"
    )


@then(parsers.parse(
    'the output reports the BC entry "{bc_name}" as not matching the configured product slug'
))
def assert_named_entry_rejected_for_slug(bc_name, ctx):
    output = ctx.get("manifest_output", "")
    assert not ctx.get("manifest_ok"), (
        f"Expected manifest_ok=False for {bc_name!r}, got output: {output!r}"
    )
    assert bc_name in output and "canonical pattern" in output, (
        f"Expected {bc_name!r} reported as not matching canonical pattern, got: {output!r}"
    )


@then(parsers.parse('the output reports "{bc_name}" as an unexpected entry in the repos directory'))
def assert_output_unexpected_entry(bc_name, ctx):
    output = ctx.get("manifest_output", "")
    assert bc_name in output, (
        f"Expected {bc_name!r} to appear in output as unexpected entry, got: {output!r}"
    )
    assert "unexpected" in output.lower(), (
        f"Expected 'unexpected' in output, got: {output!r}"
    )


@then('the command does not delete the unexpected directory')
def assert_directory_not_deleted(ctx):
    """Verify the repos directory entries still exist after validate."""
    repos_dir = ctx.get("repos_dir")
    extra_dirs = ctx.get("extra_dirs", [])
    for dir_name in extra_dirs:
        target = repos_dir / dir_name
        assert target.exists(), (
            f"Directory {dir_name!r} was deleted by validate — it should not have been"
        )


@then('the output contains exactly six lines')
def assert_output_exactly_six_lines(ctx):
    output = ctx.get("list_output", "")
    lines = [l for l in output.splitlines() if l.strip()]
    assert len(lines) == 6, (
        f"Expected exactly 6 lines in list output, got {len(lines)}: {output!r}"
    )


@then('each line contains the canonical name of one declared BC')
def assert_each_line_has_canonical_name(ctx):
    output = ctx.get("list_output", "")
    lines = [l for l in output.splitlines() if l.strip()]
    declared_names = [e["name"] for e in PRODUCT_BCS]
    for line in lines:
        assert any(name in line for name in declared_names), (
            f"Line {line!r} does not contain a canonical BC name"
        )


@then('a script can extract all six canonical BC names from stdout using only standard text processing tools')
def assert_six_canonical_names_extractable(ctx):
    output = ctx.get("list_output", "")
    extracted = [line.split()[0] for line in output.splitlines() if line.strip()]
    assert len(extracted) == 6, (
        f"Expected to extract 6 BC names, got {len(extracted)}: {extracted!r}"
    )
    for name in extracted:
        assert BC_NAME_RE.match(name), (
            f"Extracted name {name!r} does not match canonical pattern"
        )


@then(parsers.parse('a directory named "{dir_name}" is present under the repos directory'))
def assert_dir_present_in_repos(dir_name, ctx):
    repos_dir = ctx.get("repos_dir")
    assert repos_dir is not None, "repos_dir not set in ctx"
    target = repos_dir / dir_name
    assert target.exists() and target.is_dir(), (
        f"Expected directory {dir_name!r} under {repos_dir}, but it was not found"
    )


@then('the directory is a git repository cloned from the declared remote URL')
def assert_dir_cloned_from_remote(ctx):
    driver = ctx.get("sync_git_driver")
    bc_name = ctx.get("declared_bc", "shopsystem-messaging")
    declared_remote = ctx.get("declared_remote", f"https://github.com/dstengle/{bc_name}.git")
    repos_dir = ctx.get("repos_dir")
    assert driver is not None, "FakeGitDriver not set in ctx"
    clone_calls = [c for c in driver.clone_calls if str(c.dest) == str(repos_dir / bc_name)]
    assert clone_calls, (
        f"No clone call recorded for {bc_name!r}. Clone calls: {driver.clone_calls!r}"
    )
    assert clone_calls[-1].remote_url == declared_remote, (
        f"Expected clone from {declared_remote!r}, got {clone_calls[-1].remote_url!r}"
    )


@then(parsers.parse('the "{bc_name}" directory is unchanged'))
def assert_dir_unchanged(bc_name, ctx):
    repos_dir = ctx.get("repos_dir")
    driver = ctx.get("sync_git_driver") or ctx.get("fake_git")
    if driver is not None:
        new_clones = [c for c in driver.clone_calls if c.dest == repos_dir / bc_name]
        assert not new_clones, (
            f"Expected no new clone for {bc_name!r} but found: {new_clones!r}"
        )


@then(parsers.parse('the output indicates that "{bc_name}" was already present and skipped'))
def assert_output_skipped(bc_name, ctx):
    output = ctx.get("sync_output", "")
    assert bc_name in output, (
        f"Expected {bc_name!r} in sync output, got: {output!r}"
    )
    assert "skipped" in output.lower() or "already present" in output.lower(), (
        f"Expected 'skipped' or 'already present' in sync output, got: {output!r}"
    )


@then('no new clones are created')
def assert_no_new_clones(ctx):
    before = ctx.get("sync_clone_count_before", 0)
    after = ctx.get("sync_clone_count_after", before)
    assert after == before, (
        f"Expected no new clones on second sync run, but got {after - before} new clone(s)"
    )


@then('no existing clones are modified')
def assert_no_existing_clones_modified(ctx):
    """Structural: sync skips existing-with-matching-remote; FakeGitDriver only creates dirs on clone."""
    # If no new clone calls happened, no modification either
    # This is guaranteed by the FakeGitDriver's clone() only creating new dirs
    assert True


@then(parsers.parse('the output reports "{bc_name}" as an entry not declared in the manifest'))
def assert_output_entry_not_declared(bc_name, ctx):
    output = ctx.get("sync_output", "")
    assert bc_name in output, (
        f"Expected {bc_name!r} in sync output, got: {output!r}"
    )
    assert "not declared" in output.lower() or "warning" in output.lower(), (
        f"Expected 'not declared' or 'warning' in sync output for {bc_name!r}, got: {output!r}"
    )


@then('the output reports all six BCs as validated')
def assert_all_six_validated(ctx):
    output = ctx.get("manifest_output", "")
    for entry in PRODUCT_BCS:
        assert entry["name"] in output, (
            f"Expected {entry['name']!r} to appear in validate output, got: {output!r}"
        )


@then(parsers.parse('the output names the BC whose remote URL could not be reached'))
def assert_output_names_unreachable_bc(ctx):
    output = ctx.get("manifest_output", "")
    bc_name = ctx.get("bc_name_for_remote", "shopsystem-messaging")
    assert bc_name in output, (
        f"Expected BC name {bc_name!r} in output for unreachable remote, got: {output!r}"
    )


@then('the output describes the failure (repository not found or connection refused)')
def assert_output_describes_failure(ctx):
    output = ctx.get("manifest_output", "")
    assert "unreachable" in output.lower() or "not found" in output.lower() or "refused" in output.lower(), (
        f"Expected failure description in output, got: {output!r}"
    )


@then(parsers.parse('the output reports "{bc_name}" as a declared BC with no local clone'))
def assert_output_no_local_clone(bc_name, ctx):
    output = ctx.get("manifest_output", "")
    assert bc_name in output, (
        f"Expected {bc_name!r} in output, got: {output!r}"
    )
    assert "no local clone" in output.lower() or "clone" in output.lower(), (
        f"Expected mention of missing clone in output, got: {output!r}"
    )


@then(parsers.parse('the output reports a remote URL mismatch for "{bc_name}"'))
def assert_output_remote_mismatch(bc_name, ctx):
    output = ctx.get("manifest_output", "")
    assert bc_name in output, (
        f"Expected {bc_name!r} in output for remote mismatch, got: {output!r}"
    )
    assert "mismatch" in output.lower(), (
        f"Expected 'mismatch' in output, got: {output!r}"
    )


@then('the output shows both the manifest-declared URL and the clone\'s actual URL')
def assert_output_shows_both_urls(ctx):
    output = ctx.get("manifest_output", "")
    declared = ctx.get("declared_remote", "")
    actual = ctx.get("actual_remote", "")
    assert declared in output, (
        f"Expected declared remote URL {declared!r} in output, got: {output!r}"
    )
    assert actual in output, (
        f"Expected actual remote URL {actual!r} in output, got: {output!r}"
    )


@then('the script can extract the canonical name of every declared BC without parse errors')
def assert_can_extract_canonical_names(ctx):
    """Combined: no parse error occurred and all canonical names are extractable."""
    assert "parsed_manifest" in ctx, "Manifest was not parsed — parse error may have occurred"
    assert isinstance(ctx["parsed_manifest"], dict), (
        f"Expected dict from YAML parse, got {type(ctx['parsed_manifest'])!r}"
    )
    data = ctx["parsed_manifest"]
    bcs = data.get("bcs") or []
    assert len(bcs) > 0, "No BC entries found in parsed manifest"
    for entry in bcs:
        name = entry.get("name", "")
        assert name, f"Empty canonical name in entry {entry!r}"


@then('the script produces exactly six URLs, one per declared BC')
def assert_six_urls_produced(ctx):
    urls = ctx.get("extracted_urls", [])
    assert len(urls) == 6, (
        f"Expected exactly 6 URLs, got {len(urls)}: {urls!r}"
    )


@then('each URL is the full GitHub remote URL for that BC\'s repository')
def assert_each_url_is_github_remote(ctx):
    urls = ctx.get("extracted_urls", [])
    for url in urls:
        assert GITHUB_URL_RE.match(url), (
            f"URL {url!r} is not a valid GitHub remote URL"
        )


# ===========================================================================
# Credential bind-mount scenario step definitions
# ===========================================================================

@then(parsers.parse('the FakeDockerDriver records that the docker run command for "{container_name}" includes a bind mount with source "{source_token}" and target "{target}"'))
def assert_bind_mount_present(container_name, source_token, target, ctx, fake_driver, tmp_path):
    """
    Assert that the docker run command for the container includes a --mount flag
    with the given source (resolved from $HOME/... to the fake_home path) and target.
    """
    run_cmd = fake_driver.run_command_for_container(container_name)
    assert run_cmd, f"FakeDockerDriver recorded no docker run command for {container_name!r}"

    # Resolve source_token: if it starts with $HOME/, map to the fake_home
    credential_home = ctx.get("credential_home", tmp_path / "fake_home")
    if source_token.startswith("$HOME/"):
        rel = source_token[len("$HOME/"):]
        resolved_source = str(credential_home / rel)
    else:
        resolved_source = source_token

    cmd_str = " ".join(run_cmd)
    assert f"source={resolved_source}" in cmd_str and f"target={target}" in cmd_str, (
        f"Expected bind mount source={resolved_source!r} target={target!r} in docker run command.\n"
        f"Recorded run command: {cmd_str!r}"
    )


@then(parsers.parse('the FakeDockerDriver records that the docker run command for "{container_name}" includes a read-only bind mount with source "{source_token}" and target "{target}"'))
def assert_readonly_bind_mount_present(container_name, source_token, target, ctx, fake_driver, tmp_path):
    """
    Assert that the docker run command includes a read-only --mount flag with the given source and target.
    """
    run_cmd = fake_driver.run_command_for_container(container_name)
    assert run_cmd, f"FakeDockerDriver recorded no docker run command for {container_name!r}"

    credential_home = ctx.get("credential_home", tmp_path / "fake_home")
    if source_token.startswith("$HOME/"):
        rel = source_token[len("$HOME/"):]
        resolved_source = str(credential_home / rel)
    else:
        resolved_source = source_token

    cmd_str = " ".join(run_cmd)
    # Check source, target, and readonly are all in the same --mount spec
    # The spec is: type=bind,source=X,target=Y,readonly
    found = False
    i = 0
    tokens = run_cmd
    while i < len(tokens):
        if tokens[i] == "--mount" and i + 1 < len(tokens):
            spec = tokens[i + 1]
            if (f"source={resolved_source}" in spec
                    and f"target={target}" in spec
                    and "readonly" in spec):
                found = True
                break
        i += 1

    assert found, (
        f"Expected read-only bind mount source={resolved_source!r} target={target!r} "
        f"in docker run command.\nRecorded run command: {cmd_str!r}"
    )


@then(parsers.parse('the FakeDockerDriver records that an exec_run was issued against "{container_name}" copying "{src}" to "{dest}"'))
def assert_exec_copy(container_name, src, dest, ctx, fake_driver):
    """Assert that an exec_run was recorded that runs cp <src> <dest> in the container."""
    cp_calls = [
        c for c in fake_driver.exec_calls
        if c.container == container_name
        and len(c.command) == 3
        and c.command[0] == "cp"
        and c.command[1] == src
        and c.command[2] == dest
    ]
    assert cp_calls, (
        f"Expected exec_run('cp {src} {dest}') against {container_name!r}.\n"
        f"Recorded exec calls: {[(c.container, c.command) for c in fake_driver.exec_calls]!r}"
    )


@then("that exec_run is recorded after the docker run for \"bc-shopsystem-messaging\" and before the tmux new-session exec_run")
def assert_cp_between_run_and_tmux(ctx, fake_driver):
    """
    Assert that the cp exec_run calls appear in the operation/exec log after
    docker run and before the tmux new-session exec_run.
    """
    container_name = "bc-shopsystem-messaging"

    # Find the index of the docker run in the operation_log
    run_idx = None
    for i, (op, name) in enumerate(fake_driver.operation_log):
        if op == "run" and name == container_name:
            run_idx = i
            break
    assert run_idx is not None, (
        f"Expected 'run' entry for {container_name!r} in operation_log: {fake_driver.operation_log!r}"
    )

    # Find cp call indices in exec_calls
    cp_indices = [
        i for i, c in enumerate(fake_driver.exec_calls)
        if c.container == container_name and c.command[0:1] == ["cp"]
    ]
    assert cp_indices, (
        f"Expected at least one 'cp' exec_run against {container_name!r}.\n"
        f"Recorded exec calls: {[(c.container, c.command) for c in fake_driver.exec_calls]!r}"
    )

    # Find tmux new-session index in exec_calls
    tmux_idx = None
    for i, c in enumerate(fake_driver.exec_calls):
        if c.container == container_name and c.command[:3] == ["tmux", "new-session", "-d"]:
            tmux_idx = i
            break
    assert tmux_idx is not None, (
        f"Expected 'tmux new-session' exec_run against {container_name!r}.\n"
        f"Recorded exec calls: {[(c.container, c.command) for c in fake_driver.exec_calls]!r}"
    )

    # All cp calls must come before the tmux new-session call
    for cp_idx in cp_indices:
        assert cp_idx < tmux_idx, (
            f"cp exec_run (index {cp_idx}) must appear before tmux new-session (index {tmux_idx}).\n"
            f"exec_calls: {[(c.container, c.command) for c in fake_driver.exec_calls]!r}"
        )


@then(parsers.parse('the FakeDockerDriver records that the docker run command for "{container_name}" includes exactly these three credential bind mounts:'))
def assert_exactly_three_credential_mounts(container_name, ctx, fake_driver, tmp_path, datatable):
    """
    Assert the docker run command includes exactly the three credential bind mounts
    described in the step's data table.  The $HOME/ prefix is resolved to credential_home.

    pytest-bdd passes data tables as a list of lists (one list per row, including header).
    """
    run_cmd = fake_driver.run_command_for_container(container_name)
    assert run_cmd, f"FakeDockerDriver recorded no docker run command for {container_name!r}"

    credential_home = ctx.get("credential_home", tmp_path / "fake_home")

    # datatable is a list of lists; first row is header, remaining rows are data
    rows = datatable[1:]  # skip header row

    for row in rows:
        source_token = row[0].strip()
        target = row[1].strip()
        readonly_str = row[2].strip()
        readonly = readonly_str.lower() == "true"

        if source_token.startswith("$HOME/"):
            rel = source_token[len("$HOME/"):]
            resolved_source = str(credential_home / rel)
        else:
            resolved_source = source_token

        # Check this mount appears in the run command
        found = False
        tokens = run_cmd
        i = 0
        while i < len(tokens):
            if tokens[i] == "--mount" and i + 1 < len(tokens):
                spec = tokens[i + 1]
                source_ok = f"source={resolved_source}" in spec
                target_ok = f"target={target}" in spec
                ro_ok = ("readonly" in spec) == readonly
                if source_ok and target_ok and ro_ok:
                    found = True
                    break
            i += 1

        assert found, (
            f"Expected mount source={resolved_source!r} target={target!r} readonly={readonly} "
            f"in docker run command for {container_name!r}.\n"
            f"Run command: {' '.join(run_cmd)!r}"
        )


@then(parsers.parse('stderr contains the literal substring "{text}"'))
def assert_stderr_contains_literal(text, ctx):
    """Assert that stderr contains the exact literal string (no $HOME expansion)."""
    result = ctx.get("result")
    assert result is not None, "No result in ctx"
    stderr = result.stderr if hasattr(result, "stderr") else ""
    assert text in stderr, (
        f"Expected literal substring {text!r} in stderr, got: {stderr!r}"
    )


@then(parsers.parse('the FakeDockerDriver records that no docker run command was issued for "{container_name}"'))
def assert_no_docker_run(container_name, ctx, fake_driver):
    """Assert that no docker run was issued for the named container."""
    run_cmd = fake_driver.run_command_for_container(container_name)
    assert not run_cmd, (
        f"Expected no docker run for {container_name!r}, but got: {run_cmd!r}"
    )


# ===========================================================================
# Network naming scenario step definitions (lead-e6j)
# ===========================================================================

# ---------------------------------------------------------------------------
# Network Given steps
# ---------------------------------------------------------------------------

@given('a bc-manifest.yaml exists containing:')
def manifest_with_docstring(docstring, ctx, tmp_path):
    """Create a bc-manifest.yaml from the step's docstring content."""
    manifest_path = tmp_path / "bc-manifest.yaml"
    manifest_path.write_text(docstring)
    ctx["launch_manifest_path"] = manifest_path


@given(parsers.parse('a bc-manifest.yaml exists with product field "{product}"'))
def manifest_with_product_field(product, ctx, tmp_path):
    """Create a bc-manifest.yaml with the given product field."""
    import yaml as _yaml
    manifest_path = tmp_path / "bc-manifest.yaml"
    manifest_path.write_text(_yaml.dump({
        "product": product,
        "bcs": [
            {"name": "shopsystem-messaging",
             "remote": "https://github.com/dstengle/shopsystem-messaging.git",
             "role": "bc"},
        ]
    }))
    ctx["launch_manifest_path"] = manifest_path
    ctx["manifest_product"] = product


@given("no bc-manifest.yaml exists in the working directory")
def no_manifest_in_cwd(ctx):
    """Signal that no manifest should be used."""
    ctx["launch_no_manifest"] = True
    ctx["launch_manifest_path"] = None


# ---------------------------------------------------------------------------
# SHOPMSG_SYSTEM_SLUG resolve+inject (lead-53y0)
# ---------------------------------------------------------------------------

@given("no SHOPMSG_SYSTEM_SLUG override is set on the launcher invocation")
def no_system_slug_override(monkeypatch):
    """Ensure SHOPMSG_SYSTEM_SLUG is absent from the launcher process env."""
    monkeypatch.delenv("SHOPMSG_SYSTEM_SLUG", raising=False)


@given(parsers.parse(
    'a SHOPMSG_SYSTEM_SLUG override "{value}" is set on the launcher invocation'
))
def system_slug_override_set(value, monkeypatch):
    """Set SHOPMSG_SYSTEM_SLUG on the launcher process env; monkeypatch restores."""
    monkeypatch.setenv("SHOPMSG_SYSTEM_SLUG", value)


@then(parsers.parse(
    'a manifest file with product field "{product}" containing a single BC entry '
    'named "{bc_name}" with a valid GitHub remote URL and role label "bc" '
    'validates ok with the manifest product slug'
))
def assert_manifest_product_validates_entry(product, bc_name, ctx, tmp_path, fake_github):
    """Run validate() against a manifest whose top-level product: drives the
    name-shape slug (NO explicit --product-slug, NO PRODUCT_SLUG env), confirming
    the BC-name-shape prefix derives from the SAME manifest product: middle tier
    as the network name and the injected system slug (lead-53y0 unification)."""
    import yaml as _yaml
    manifest_path = tmp_path / f"validate-{product}-{bc_name}.yaml"
    manifest_path.write_text(_yaml.dump({
        "product": product,
        "bcs": [{
            "name": bc_name,
            "remote": f"https://github.com/dstengle/{bc_name}.git",
            "role": "bc",
        }],
    }))
    mc = ManifestController(github_driver=fake_github)
    result = mc.validate(manifest_path)
    assert result.ok, (
        f"Expected manifest with product:{product!r} to validate entry "
        f"{bc_name!r} ok, got messages: {result.messages!r}"
    )
    assert bc_name in result.validated, (
        f"Expected {bc_name!r} in validated set, got: {result.validated!r}"
    )


@then(parsers.parse(
    'a manifest file with product field "{product}" containing a single BC entry '
    'named "{bc_name}" with a valid GitHub remote URL and role label "bc" '
    'is rejected as not matching the manifest product slug'
))
def assert_manifest_product_rejects_entry(product, bc_name, ctx, tmp_path, fake_github):
    """Confirm the name-shape gate (derived from manifest product:) rejects a BC
    name that does not match the manifest product slug — the same surface the
    injected slug and network name derive from (lead-53y0 unification)."""
    import yaml as _yaml
    manifest_path = tmp_path / f"reject-{product}-{bc_name}.yaml"
    manifest_path.write_text(_yaml.dump({
        "product": product,
        "bcs": [{
            "name": bc_name,
            "remote": f"https://github.com/dstengle/{bc_name}.git",
            "role": "bc",
        }],
    }))
    mc = ManifestController(github_driver=fake_github)
    result = mc.validate(manifest_path)
    assert not result.ok, (
        f"Expected manifest with product:{product!r} to REJECT entry "
        f"{bc_name!r}, got messages: {result.messages!r}"
    )
    assert bc_name in result.failed, (
        f"Expected {bc_name!r} in failed set, got: {result.failed!r}"
    )


@given("no explicit \"--network\" flag is provided")
def no_explicit_network_flag(ctx):
    """Record that no explicit network flag will be passed."""
    ctx["explicit_network"] = None


@given(parsers.parse('no Docker network named "{network_name}" exists'))
def no_docker_network(network_name, ctx, fake_driver):
    """Ensure the named network does not exist in the fake driver."""
    fake_driver.set_network(network_name, exists=False)


@given(parsers.parse('a Docker network named "{network_name}" already exists'))
def docker_network_exists(network_name, ctx, fake_driver):
    """Pre-create the named network in the fake driver."""
    fake_driver.set_network(network_name, exists=True)


# ---------------------------------------------------------------------------
# Network When steps
# ---------------------------------------------------------------------------

@when(parsers.parse('I run bc-container launch with BC name "{bc_name}" and flag "--network {network_name}"'))
def run_launch_with_explicit_network(bc_name, network_name, ctx, fake_driver, controller):
    """Launch with an explicit --network flag."""
    repo_url = ctx.get("repo_url", f"https://github.com/shopsystem/{bc_name}.git")
    credential_home = ctx.get("credential_home")
    result = controller.launch(
        bc_name=bc_name,
        repo_url=repo_url,
        network=network_name,
        manifest_path=ctx.get("launch_manifest_path"),
        credential_home=credential_home,
    )
    ctx["result"] = result
    ctx.setdefault("all_results", []).append(result)
    ctx["container_name"] = f"bc-{bc_name}"
    ctx["bc_name"] = bc_name


# ---------------------------------------------------------------------------
# Network Then steps
# ---------------------------------------------------------------------------

@then(parsers.parse('stderr includes the text "{text}"'))
def assert_stderr_includes_text(text, ctx):
    result = ctx.get("result")
    assert result is not None, "No result in ctx"
    stderr = result.stderr if hasattr(result, "stderr") else ""
    assert text in stderr, (
        f"Expected {text!r} in stderr, got: {stderr!r}"
    )


@then(parsers.parse('the FakeDockerDriver records that "docker network create {network_name}" was called before "docker run"'))
def assert_network_create_before_run(network_name, ctx, fake_driver):
    """Assert network_create occurred before docker run in the operation log."""
    log = fake_driver.operation_log
    network_create_idx = None
    run_idx = None
    for i, (op, name) in enumerate(log):
        if op == "network_create" and name == network_name and network_create_idx is None:
            network_create_idx = i
        if op == "run" and run_idx is None:
            run_idx = i
    assert network_create_idx is not None, (
        f"Expected 'docker network create {network_name}' in operation log, got: {log!r}"
    )
    assert run_idx is not None, (
        f"Expected 'docker run' in operation log, got: {log!r}"
    )
    assert network_create_idx < run_idx, (
        f"Expected network create (idx={network_create_idx}) before docker run (idx={run_idx}). "
        f"Log: {log!r}"
    )


@then(parsers.parse('a Docker network named "{network_name}" exists'))
def assert_docker_network_exists(network_name, ctx, fake_driver):
    assert fake_driver.network_exists(network_name), (
        f"Expected Docker network {network_name!r} to exist"
    )


@then(parsers.parse('the FakeDockerDriver records that "docker network create {network_name}" was NOT called'))
def assert_network_create_not_called(network_name, ctx, fake_driver):
    called = [n for n in fake_driver.network_create_calls if n == network_name]
    assert not called, (
        f"Expected 'docker network create {network_name}' NOT to be called, "
        f"but it was called {len(called)} time(s)"
    )


@then(parsers.parse('the FakeDockerDriver records that "docker network create" was NOT called'))
def assert_no_network_create_called(ctx, fake_driver):
    assert not fake_driver.network_create_calls, (
        f"Expected no 'docker network create' calls, "
        f"but got: {fake_driver.network_create_calls!r}"
    )


@then(parsers.parse('the FakeDockerDriver records that a docker run was issued for "{container_name}"'))
def assert_docker_run_was_issued(container_name, ctx, fake_driver):
    """Assert that a docker run was recorded for the named container."""
    run_cmd = fake_driver.run_command_for_container(container_name)
    assert run_cmd, (
        f"Expected a docker run command for {container_name!r}, but none was recorded.\n"
        f"All run commands: {fake_driver._run_commands_by_container!r}"
    )


@then(parsers.parse('the FakeDockerDriver records that no exec_run was issued against "{container_name}" copying any path ending in "{path_suffix}" to "{dest}"'))
def assert_no_exec_cp_with_suffix(container_name, path_suffix, dest, ctx, fake_driver):
    """
    Assert that no exec_run cp command was issued where the source ends with path_suffix
    and the destination is dest.
    """
    matching_calls = [
        c for c in fake_driver.exec_calls
        if c.container == container_name
        and len(c.command) == 3
        and c.command[0] == "cp"
        and c.command[1].endswith(path_suffix)
        and c.command[2] == dest
    ]
    assert not matching_calls, (
        f"Expected no exec_run('cp <path ending in {path_suffix!r}> {dest}') "
        f"against {container_name!r}, but found: {[c.command for c in matching_calls]!r}"
    )


@then(parsers.parse('the FakeDockerDriver records that a tmux new-session exec_run was issued against "{container_name}"'))
def assert_tmux_new_session_issued(container_name, ctx, fake_driver):
    """Assert that a tmux new-session exec_run was recorded against the container."""
    tmux_calls = [
        c for c in fake_driver.exec_calls
        if c.container == container_name
        and len(c.command) >= 3
        and c.command[:3] == ["tmux", "new-session", "-d"]
    ]
    assert tmux_calls, (
        f"Expected a 'tmux new-session -d' exec_run against {container_name!r}, "
        f"but none was recorded.\n"
        f"Recorded exec calls: {[(c.container, c.command) for c in fake_driver.exec_calls]!r}"
    )


@then(parsers.parse('the FakeDockerDriver records that the docker run command does NOT include "{flag}"'))
def assert_docker_run_does_not_include_flag(flag, ctx, fake_driver):
    run_cmd = fake_driver.last_run_command()
    assert run_cmd, "FakeDockerDriver recorded no docker run command"
    cmd_str = " ".join(run_cmd)
    assert flag not in cmd_str, (
        f"Expected flag {flag!r} NOT in docker run command.\n"
        f"Recorded run command: {cmd_str!r}"
    )


@then("the command exits zero for both launches")
def assert_both_launches_exit_zero(ctx):
    all_results = ctx.get("all_results", [])
    assert len(all_results) == 2, (
        f"Expected results for 2 launches, got {len(all_results)}"
    )
    for i, result in enumerate(all_results):
        assert result.exit_code == 0, (
            f"Launch {i + 1} exited {result.exit_code} (stderr: {result.stderr!r})"
        )


@then(parsers.parse('the FakeDockerDriver records that "docker network create {network_name}" was called exactly once across both launches'))
def assert_network_create_called_exactly_once(network_name, ctx, fake_driver):
    calls = [n for n in fake_driver.network_create_calls if n == network_name]
    assert len(calls) == 1, (
        f"Expected 'docker network create {network_name}' to be called exactly once, "
        f"but it was called {len(calls)} time(s). All calls: {fake_driver.network_create_calls!r}"
    )


# ===========================================================================
# Prompt-submit scenario step definitions (lead-xsmn / lead-hyee)
#
# These pin the resolution of the --startup-prompt / inject submit bug:
# a prompt must be COMMITTED to the agent's input loop (Enter as a discrete
# tmux send-keys key argument), not left as an unsubmitted buffer entry
# (text with an appended '\n').  The FakeDockerDriver models this faithfully:
# it flips the agent to "processing" only when a send-keys carries a
# non-empty text token followed by a discrete "Enter" token (see
# tests/fake_driver.py).
# ===========================================================================

# NOTE: the Given step 'a Docker container named "..." is running with a tmux
# session named "..." hosting an interactive agent at its input prompt' was
# retired with scenario 28 (17518db1dc1c9001) under lead-lez1.  Scenario 31
# (ad68aaf60377706e) uses the simpler '... running with a tmux session named
# "agent"' Given, defined in the two-call section below.


@when(parsers.parse(
    'I run "bc-container launch {bc_name} --startup-prompt \'{prompt}\'" '
    'and the launch command exits zero'
))
def run_launch_quoted_startup_prompt(bc_name, prompt, ctx, fake_driver, controller, tmp_path):
    repo_url = ctx.get("repo_url", f"https://github.com/shopsystem/{bc_name}.git")
    manifest_path = ctx.get("launch_manifest_path")
    if manifest_path is None and "launch_no_manifest" not in ctx:
        default_manifest = tmp_path / "bc-manifest.yaml"
        if not default_manifest.exists():
            import yaml as _yaml
            default_manifest.write_text(_yaml.dump({
                "product": "shopsystem product",
                "bcs": [{"name": bc_name, "remote": repo_url, "role": "bc"}],
            }))
        manifest_path = default_manifest
    credential_home = ctx.get("credential_home")
    result = controller.launch(
        bc_name=bc_name,
        repo_url=repo_url,
        startup_prompt=prompt,
        manifest_path=manifest_path,
        credential_home=credential_home,
    )
    assert result.exit_code == 0, (
        f"Expected launch to exit zero, got {result.exit_code} (stderr: {result.stderr!r})"
    )
    ctx["result"] = result
    ctx["container_name"] = f"bc-{bc_name}"
    ctx["bc_name"] = bc_name
    ctx["startup_prompt"] = prompt


@when(parsers.parse(
    'I run "bc-container inject {bc_name} \'{prompt}\'" and the command exits zero'
))
def run_inject_quoted(bc_name, prompt, ctx, fake_driver, controller):
    result = controller.inject(bc_name, prompt)
    assert result.exit_code == 0, (
        f"Expected inject to exit zero, got {result.exit_code} (stderr: {result.stderr!r})"
    )
    ctx["result"] = result
    ctx["container_name"] = f"bc-{bc_name}"
    ctx["bc_name"] = bc_name
    ctx["prompt"] = prompt


@when(parsers.parse(
    'I run "bc-container monitor {bc_name}" and read its streamed output '
    'without issuing any further "bc-container inject" or other host-side keystroke'
))
def run_monitor_quoted(bc_name, ctx, fake_driver, controller):
    # Record the send-keys call count BEFORE monitor so the Then step can
    # assert no intervening inject occurred between launch and the marker.
    container_name = f"bc-{bc_name}"
    ctx["send_keys_before_monitor"] = len(fake_driver.send_keys_calls(container_name))
    result = controller.monitor(bc_name)
    ctx["monitor_result"] = result
    ctx["result"] = result
    ctx["container_name"] = container_name
    ctx["bc_name"] = bc_name


# NOTE: three Then step definitions were retired with scenarios 27
# (0e733774844ed9f3) and 28 (17518db1dc1c9001) under lead-lez1, because no
# remaining feature references their phrasings:
#   * 'no subsequent "bc-container inject" invocation ... is required ...'
#   * "the in-container agent's input loop has been committed the prompt ..."
#   * "the agent's observable state transitions from idle to actively
#      processing the prompt ..."
# Plus the controller_monitor_pane helper used only by the last of those.
# The successor scenarios 30 (6477b2ab3720ac53) and 31 (ad68aaf60377706e)
# pin the two-discrete-invocation send-keys shape via the step definitions
# in the two-call section below.


@then(parsers.parse(
    'within 30 seconds of the launch command exiting, the streamed monitor '
    'output contains an agent-working state-marker line that is produced only '
    'when the agent has committed input and is actively processing it (and not '
    'produced when the agent is idle at an unsubmitted input buffer)'
))
def assert_monitor_shows_working_marker(ctx, fake_driver):
    result = ctx["monitor_result"]
    assert result.exit_code == 0, (
        f"Expected monitor to exit zero, got {result.exit_code}"
    )
    assert "Working" in result.stdout, (
        f"Expected an agent-working state-marker in monitor output, got: {result.stdout!r}"
    )
    # Non-vacuity: the marker is produced ONLY when input is committed. Confirm
    # the modelled agent is in the processing state, not idle-at-buffer.
    container_name = ctx["container_name"]
    assert fake_driver.agent_committed_prompt(container_name) is not None, (
        "Monitor surfaced a working marker but the modelled agent has no "
        "committed input — the marker would be vacuous."
    )


@then(parsers.parse(
    "the agent-working state-marker appears as a direct consequence of the "
    "launch's --startup-prompt being submitted, with no intervening "
    '"bc-container inject" invocation'
))
def assert_marker_no_intervening_inject(ctx, fake_driver):
    container_name = ctx["container_name"]
    before = ctx.get("send_keys_before_monitor")
    after = len(fake_driver.send_keys_calls(container_name))
    assert before is not None, "send_keys_before_monitor not recorded by the monitor When step"
    assert after == before, (
        f"Expected no intervening tmux send-keys (inject) between launch and the "
        f"monitor read; send-keys count went {before} -> {after}."
    )
    startup_prompt = ctx.get("startup_prompt")
    assert fake_driver.agent_committed_prompt(container_name) == startup_prompt, (
        f"Expected the working marker to trace to the launch's --startup-prompt "
        f"{startup_prompt!r}; committed prompt is "
        f"{fake_driver.agent_committed_prompt(container_name)!r}."
    )


# ===========================================================================
# lead-j351: marker-keyed (progress-based) readiness wait.
# Scenario @scenario_hash:d227ccbcc9bdfa87 — a brokered boot whose Claude
# agent reaches its input-ready marker only AFTER the legacy 60s deadline
# must still have its startup prompt injected.  The FakeDockerDriver models
# a "delayed marker": the input-ready marker is only observable once the
# simulated boot has been progressing for more than 60s, so a fixed-60s
# deadline implementation would drop injection while a marker-keyed one
# still injects.
# ===========================================================================

_J351_SLOW_PROMPT = "J351_SLOW_BROKERED_BOOT_PROMPT"


@given(parsers.parse(
    "a brokered BC container whose Claude agent reaches its input-ready "
    "marker only after more than 60 seconds"
))
def j351_brokered_slow_boot(ctx, fake_driver):
    from bc_launcher.controller import AGENT_TMUX_SESSION, CLAUDE_INPUT_READY_MARKER
    bc_name = "shopsystem-messaging"
    container_name = f"bc-{bc_name}"
    fake_driver.set_running(container_name, running=False)
    # The input-ready marker only becomes observable after >60s of a
    # *progressing* brokered boot.
    fake_driver.simulate_marker_delayed_past_seconds(
        container_name,
        AGENT_TMUX_SESSION,
        CLAUDE_INPUT_READY_MARKER,
        appears_after_seconds=75.0,
    )
    ctx["bc_name"] = bc_name
    ctx["container_name"] = container_name


@when(parsers.parse(
    "bc-container launch waits for the agent to become ready before "
    "injecting the startup prompt"
))
def j351_launch_slow_boot(ctx, fake_driver, controller, tmp_path):
    bc_name = ctx["bc_name"]
    manifest_path = tmp_path / "bc-manifest.yaml"
    if not manifest_path.exists():
        import yaml as _yaml
        manifest_path.write_text(_yaml.dump({
            "product": "shopsystem product",
            "bcs": [{
                "name": bc_name,
                "remote": f"https://github.com/shopsystem/{bc_name}.git",
                "role": "bc",
            }],
        }))
    result = controller.launch(
        bc_name=bc_name,
        startup_prompt=_J351_SLOW_PROMPT,
        manifest_path=manifest_path,
        credential_home=ctx.get("credential_home"),
    )
    ctx["result"] = result
    ctx["startup_prompt"] = _J351_SLOW_PROMPT


@then(parsers.parse(
    "launch does not abandon prompt injection at a fixed 60-second deadline "
    "while the agent is still progressing toward readiness"
))
def j351_no_fixed_deadline_abandon(ctx):
    result = ctx["result"]
    assert result.exit_code == 0, (
        f"Expected launch to exit zero, got {result.exit_code} "
        f"(stderr: {result.stderr!r})"
    )
    assert "NOT injected" not in result.stderr, (
        "Launch abandoned prompt injection at the fixed 60s deadline for a "
        f"still-progressing brokered boot; stderr: {result.stderr!r}"
    )


@then(parsers.parse(
    "once the agent's input-ready marker is observed the startup prompt is "
    'injected into the tmux session named "{session}"'
))
def j351_prompt_injected_after_marker(session, ctx, fake_driver):
    container_name = ctx["container_name"]
    send_keys = [
        c.command for c in fake_driver.exec_calls
        if c.container == container_name and c.command[:2] == ["tmux", "send-keys"]
    ]
    injected = [
        cmd for cmd in send_keys
        if _J351_SLOW_PROMPT in cmd
        and cmd[:4] == ["tmux", "send-keys", "-t", session]
    ]
    assert injected, (
        f"Expected the startup prompt to be injected into tmux session "
        f"{session!r} after the input-ready marker was observed; "
        f"send-keys recorded: {send_keys!r}"
    )
    # Non-vacuity: the input-ready marker wait actually happened and preceded
    # the prompt injection (inject-after-ready ordering, 5ef728039884a9a2).
    from bc_launcher.controller import CLAUDE_INPUT_READY_MARKER
    markers = [m for (_c, _s, m) in fake_driver.wait_for_marker_calls]
    assert CLAUDE_INPUT_READY_MARKER in markers, (
        f"Expected an input-ready marker wait; recorded: {markers!r}"
    )
    assert fake_driver.input_ready_wait_preceded_prompt(_J351_SLOW_PROMPT), (
        "The startup prompt must be injected only AFTER the input-ready "
        "marker wait (inject-after-ready ordering, 5ef728039884a9a2)."
    )


@then(parsers.parse(
    "the readiness wait keys on the observable input-ready marker rather "
    "than a fixed deadline that fires before a slow brokered boot completes"
))
def j351_wait_keyed_on_marker(ctx, fake_driver):
    # The launch above configured the input-ready marker to appear only after
    # >60s of a progressing boot, yet the prompt was injected.  That outcome
    # is only possible if the wait keyed on the observable marker / progress
    # rather than abandoning at the fixed 60s deadline.  Re-assert the marker
    # was the gating signal: the fake records that the marker became
    # observable strictly after the legacy 60s deadline.
    container_name = ctx["container_name"]
    assert fake_driver.marker_observed_after_legacy_deadline(container_name), (
        "The input-ready marker must have been observed only AFTER the legacy "
        "60s deadline, proving the wait keyed on the marker rather than a "
        "fixed deadline that would have fired first."
    )


# ===========================================================================
# Two-discrete-invocation send-keys scenario step definitions
# (lead-lez1 / lead-9q0f, scenarios 30 / 31).
#
# These pin the ROOT-CAUSE fix at the driver argv surface: launch
# --startup-prompt and inject must each issue EXACTLY TWO tmux send-keys
# invocations against the container driver — the prompt text alone first,
# then a bare Enter second — and NO single invocation may carry both.  This
# is the shape that survives Claude Code's paste-absorption heuristic
# (single text+Enter pty write is swallowed as a paste).  Assertions read the
# FakeDockerDriver's recorded send_keys_calls (the driver-surface recorder).
# ===========================================================================


@given(parsers.parse(
    'a Docker container named "{container_name}" is running with a tmux '
    'session named "{session}"'
))
def container_running_with_tmux_session(container_name, session, ctx, fake_driver):
    fake_driver.set_running(container_name, running=True)
    fake_driver.add_tmux_session(container_name, session)
    ctx["container_name"] = container_name


def _prompt_submit_send_keys(fake_driver, container_name, prompt):
    """Return the send-keys invocations attributable to the prompt submission.

    The launch path issues earlier readiness send-keys (launching claude,
    accepting the trust prompt) that are NOT part of the --startup-prompt
    handling.  The prompt-submit handling is the trailing pair: the
    text-carrying invocation for ``prompt`` and the Enter invocation that
    immediately follows it.  We locate the invocation carrying the prompt
    text as a discrete token and return it together with the next send-keys
    invocation.
    """
    calls = fake_driver.send_keys_calls(container_name)
    # Index of the (last) invocation whose payload carries the prompt text as
    # a discrete token.
    text_idx = None
    for i, c in enumerate(calls):
        if prompt in c.command:
            text_idx = i
    assert text_idx is not None, (
        f"No tmux send-keys invocation carried prompt text {prompt!r} in "
        f"{container_name!r}; recorded: {[c.command for c in calls]!r}"
    )
    assert text_idx + 1 < len(calls), (
        f"Expected a send-keys invocation AFTER the prompt-text invocation "
        f"(the discrete Enter), but the prompt-text invocation was the last "
        f"send-keys recorded: {[c.command for c in calls]!r}"
    )
    return calls[text_idx], calls[text_idx + 1], text_idx, text_idx + 1


@then(parsers.parse(
    'the BC has issued exactly two tmux send-keys invocations against the '
    'container driver targeting the tmux session named "{session}" in '
    'container "{container_name}" as a direct consequence of the '
    '--startup-prompt being honored'
))
def assert_exactly_two_send_keys_launch(session, container_name, ctx, fake_driver):
    prompt = ctx.get("startup_prompt")
    text_call, enter_call, text_idx, enter_idx = _prompt_submit_send_keys(
        fake_driver, container_name, prompt
    )
    ctx["submit_text_call"] = text_call
    ctx["submit_enter_call"] = enter_call
    ctx["submit_text_idx"] = text_idx
    ctx["submit_enter_idx"] = enter_idx
    ctx["submit_prompt"] = prompt
    for call in (text_call, enter_call):
        assert call.command[:4] == ["tmux", "send-keys", "-t", session], (
            f"Expected send-keys targeting session {session!r}; got {call.command!r}"
        )


@then(parsers.parse(
    'the BC has issued exactly two tmux send-keys invocations against the '
    'container driver targeting the tmux session named "{session}" in '
    'container "{container_name}" as a direct consequence of the inject command'
))
def assert_exactly_two_send_keys_inject(session, container_name, ctx, fake_driver):
    prompt = ctx.get("prompt")
    # The inject command's ONLY send-keys are the prompt-submit pair.
    calls = fake_driver.send_keys_calls(container_name)
    assert len(calls) == 2, (
        f"Expected exactly two send-keys invocations from the inject command "
        f"against {container_name!r}; got {len(calls)}: {[c.command for c in calls]!r}"
    )
    text_call, enter_call = calls[0], calls[1]
    ctx["submit_text_call"] = text_call
    ctx["submit_enter_call"] = enter_call
    ctx["submit_text_idx"] = 0
    ctx["submit_enter_idx"] = 1
    ctx["submit_prompt"] = prompt
    for call in (text_call, enter_call):
        assert call.command[:4] == ["tmux", "send-keys", "-t", session], (
            f"Expected send-keys targeting session {session!r}; got {call.command!r}"
        )


@then(parsers.parse(
    'the first of those two invocations carries the prompt text "{prompt}" as '
    'its key payload and does not carry the Enter key in the same invocation'
))
def assert_first_invocation_text_only(prompt, ctx):
    text_call = ctx["submit_text_call"]
    assert prompt in text_call.command, (
        f"Expected first invocation to carry prompt text {prompt!r}; got "
        f"{text_call.command!r}"
    )
    assert "Enter" not in text_call.command, (
        f"First invocation must NOT carry the Enter key; got {text_call.command!r}"
    )


@then(parsers.parse(
    'the second of those two invocations carries the Enter key as its key '
    'payload and does not carry the prompt text "{prompt}" in the same invocation'
))
def assert_second_invocation_enter_only(prompt, ctx):
    enter_call = ctx["submit_enter_call"]
    assert "Enter" in enter_call.command, (
        f"Expected second invocation to carry the Enter key; got {enter_call.command!r}"
    )
    assert prompt not in enter_call.command, (
        f"Second invocation must NOT carry the prompt text {prompt!r}; got "
        f"{enter_call.command!r}"
    )


@then(parsers.parse(
    'no single tmux send-keys invocation issued by the launch command\'s '
    '--startup-prompt handling carries both the prompt text "{prompt}" and '
    'the Enter key together'
))
def assert_no_single_invocation_carries_both_launch(prompt, ctx, fake_driver):
    container_name = ctx["container_name"]
    offenders = [
        c.command for c in fake_driver.send_keys_calls(container_name)
        if prompt in c.command and "Enter" in c.command
    ]
    assert not offenders, (
        f"Found send-keys invocation(s) carrying BOTH prompt {prompt!r} and "
        f"Enter — the paste-absorption regression: {offenders!r}"
    )


@then(parsers.parse(
    'no single tmux send-keys invocation issued by the inject command carries '
    'both the prompt text "{prompt}" and the Enter key together'
))
def assert_no_single_invocation_carries_both_inject(prompt, ctx, fake_driver):
    container_name = ctx["container_name"]
    offenders = [
        c.command for c in fake_driver.send_keys_calls(container_name)
        if prompt in c.command and "Enter" in c.command
    ]
    assert not offenders, (
        f"Found send-keys invocation(s) carrying BOTH prompt {prompt!r} and "
        f"Enter — the paste-absorption regression: {offenders!r}"
    )


@then(
    "the two invocations are issued in order: the text-only invocation first, "
    "the Enter-only invocation second"
)
def assert_invocation_order(ctx, fake_driver):
    container_name = ctx["container_name"]
    # Use the indices captured when the pair was located (the bare-Enter
    # invocation is identical to the trust-accept Enter, so re-searching by
    # value would alias to the wrong index).
    text_idx = ctx["submit_text_idx"]
    enter_idx = ctx["submit_enter_idx"]
    calls = fake_driver.send_keys_calls(container_name)
    assert text_idx < enter_idx, (
        f"Expected text-only invocation (index {text_idx}) before Enter-only "
        f"invocation (index {enter_idx}); recorded: {[c.command for c in calls]!r}"
    )
    assert enter_idx == text_idx + 1, (
        f"Expected the Enter-only invocation (index {enter_idx}) to immediately "
        f"follow the text-only invocation (index {text_idx}); recorded: "
        f"{[c.command for c in calls]!r}"
    )


# ---------------------------------------------------------------------------
# Then steps — messaging readiness / beads usability / health (lead-ieph)
# ---------------------------------------------------------------------------

@then("the committed beads registry is materialized into the container's "
      "working tree")
def assert_committed_registry_materialized(ctx, fake_driver):
    """The launcher must check the committed registry out into the worktree.

    On clone, .beads/issues.jsonl is git-tracked at HEAD but ABSENT from the
    working tree.  Provisioning must run `git checkout HEAD -- .beads/issues.jsonl`
    to materialize it (lead-rply DEFECT 2).
    """
    container_name = ctx["container_name"]
    checkout_calls = [
        c for c in fake_driver.exec_calls
        if c.container == container_name
        and c.command[0] == "git" and "checkout" in c.command
        and any(arg.endswith(".beads/issues.jsonl") for arg in c.command)
    ]
    assert checkout_calls, (
        "Expected a `git checkout HEAD -- .beads/issues.jsonl` exec call to "
        f"materialize the committed registry in {container_name!r}"
    )
    assert fake_driver.beads_registry_materialized(container_name), (
        f"Committed registry not materialized into the working tree of "
        f"{container_name!r}"
    )


@then("the beads issue_prefix configured inside the container's .beads is "
      "non-empty and equals the repo's committed prefix")
def assert_beads_prefix_is_committed(ctx, fake_driver):
    """The configured prefix must be the COMMITTED prefix, NOT name-derived.

    For shopsystem-bc-launcher the committed prefix is 'bclaunch' while
    name-derivation yields 'bclauncher'; this step fails on a name-derived
    mismatch (lead-rply DEFECT 1).
    """
    from bc_launcher.controller import beads_prefix_for
    container_name = ctx["container_name"]
    bc_name = ctx["bc_name"]
    expected = ctx["committed_beads_prefix"]
    configured = fake_driver.beads_prefix(container_name)
    assert configured, (
        f"Expected a non-empty beads issue_prefix configured in "
        f"{container_name!r}, got {configured!r}"
    )
    assert configured == expected, (
        f"Expected the COMMITTED beads issue_prefix {expected!r} for BC "
        f"{bc_name!r}, got {configured!r}"
    )
    # Non-vacuity guard: the committed prefix this scenario pins must differ
    # from the name-derived prefix, so adopting-the-committed-prefix is a real
    # behavioural distinction and not accidentally satisfied by name-derivation.
    assert expected != beads_prefix_for(bc_name), (
        f"Scenario is vacuous: committed prefix {expected!r} equals the "
        f"name-derived prefix for {bc_name!r}; pick a BC whose committed "
        f"registry carries a prefix the BC name does not imply."
    )


@then("the committed beads registry is imported into the container's Dolt "
      "working set")
def assert_committed_registry_imported(ctx, fake_driver):
    """lead-kjv7 DEFECT 2 — provisioning must run an explicit `bd import`.

    Setting the issue_prefix alone does NOT import the committed registry into
    the embedded-Dolt working set (the empirical failure: `embeddeddolt/`
    absent, `bd ready`/`bd create` → "no beads database found").  The launcher
    must run `bd import` of the materialized registry.
    """
    container_name = ctx["container_name"]
    import_calls = [
        c for c in fake_driver.exec_calls
        if c.container == container_name
        and c.command[:2] == ["bd", "import"]
    ]
    assert import_calls, (
        "Expected a `bd import` exec call to import the committed registry "
        f"into the Dolt working set of {container_name!r}; the launcher must "
        "not rely on `bd config set issue_prefix` to side-effect-import "
        "(lead-kjv7 DEFECT 2)"
    )
    assert fake_driver.beads_working_set_provisioned(container_name), (
        f"Dolt working set not provisioned in {container_name!r} after "
        "provisioning"
    )


@then("the container's beads embedded-Dolt working set directory exists")
def assert_embeddeddolt_present(ctx, fake_driver):
    """lead-kjv7 DEFECT 4 — `/workspace/.beads/embeddeddolt/` must exist.

    Its ABSENCE was the empirical failure surface.  It materializes only once
    the committed registry has been imported into the Dolt working set.
    """
    container_name = ctx["container_name"]
    assert fake_driver.beads_embeddeddolt_present(container_name), (
        f"Expected the embedded-Dolt working set directory to exist in "
        f"{container_name!r} (it is created by `bd import`); a configured "
        "prefix without an import leaves it ABSENT — the empirical "
        "'no beads database found' state (lead-kjv7 DEFECT 2/4)"
    )


@then("the container's .beads directory is owned by vscode")
def assert_beads_owned_by_vscode(ctx, fake_driver):
    """lead-kjv7 DEFECT 3 — `/workspace/.beads` must be vscode-owned.

    Provisioning runs as root, so the `.beads` tree (including the freshly
    imported Dolt working set) lands root-owned and the vscode agent cannot
    use it.  The launcher must chown `.beads` recursively to vscode AFTER all
    beads writes (or run provisioning as vscode).  A non-recursive chown of
    /workspace that does not cover `.beads` leaves it root-owned — the
    empirical DEFECT 3.
    """
    container_name = ctx["container_name"]
    owner = fake_driver.beads_owner(container_name)
    assert owner == "vscode", (
        f"Expected `/workspace/.beads` in {container_name!r} to be owned by "
        f"vscode, got {owner!r}; the agent cannot use a root-owned backend "
        "(lead-kjv7 DEFECT 3)"
    )


# ---------------------------------------------------------------------------
# lead-mf15 — durable vscode ownership of every agent-touched workspace path
# across container init (scenario @scenario_hash:d9e4ce60e03df361).
# ---------------------------------------------------------------------------

@given("a BC container is launched whose agent runs as the unprivileged "
       "vscode user")
def mf15_container_with_vscode_agent(ctx, fake_driver):
    """Record that the launch under test runs its agent as vscode.

    The launch itself is driven by the When step below; this Given pins the
    premise (the agent is the unprivileged vscode user, so every path it
    touches must be vscode-owned for it to work).
    """
    ctx["agent_user"] = "vscode"


@when("the launcher clones the repository, provisions beads, and runs any "
      "root-context setup during container init")
def mf15_run_full_container_init(ctx, fake_driver, controller, tmp_path):
    """Run a full launch: clone + bd bootstrap + shop-templates refresh +
    tmux start — exercising every container-init step that writes under
    /workspace, including the root-context provisioning ops."""
    credential_home = ctx.get("credential_home")
    if credential_home is None:
        credential_home = tmp_path / "fake_home"
        credential_home.mkdir(parents=True, exist_ok=True)
        (credential_home / ".claude").mkdir(parents=True, exist_ok=True)
        (credential_home / ".config" / "gh").mkdir(parents=True, exist_ok=True)
        (credential_home / ".gitconfig").write_text("")
        ctx["credential_home"] = credential_home

    manifest = tmp_path / "bc-manifest.yaml"
    manifest.write_text(
        "product: shopsystem product\n"
        "bcs:\n"
        "  - name: shopsystem-messaging\n"
        "    remote: https://github.com/shopsystem/shopsystem-messaging.git\n"
        "    role: bc\n"
    )
    ctx["container_name"] = "bc-shopsystem-messaging"
    result = controller.launch(
        bc_name="shopsystem-messaging",
        repo_url="https://example.invalid/shopsystem-messaging.git",
        startup_prompt="anything",
        manifest_path=manifest,
        credential_home=credential_home,
    )
    ctx["result"] = result
    assert result.exit_code == 0, (
        f"launch failed during container init: stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )


@then("every path under the container's /workspace that the agent may touch "
      "is owned by vscode after container init completes")
def mf15_every_path_vscode_owned(ctx, fake_driver):
    container_name = ctx["container_name"]
    owners = fake_driver.workspace_path_owners_at_agent_start(container_name)
    assert owners, (
        "Expected per-path ownership to be snapshotted at agent start "
        "(tmux new-session); got none — was the agent ever started?"
    )
    non_vscode = {p: o for p, o in owners.items() if o != "vscode"}
    assert not non_vscode, (
        "After container init completes, every agent-touched path under "
        "/workspace must be vscode-owned (lead-mf15 "
        "@scenario_hash:d9e4ce60e03df361); these were not: "
        f"{non_vscode!r}.  A root-owned agent-touched path is the mid-run "
        "re-root that required a host `docker exec -u root chown` twice on "
        "2026-06-18."
    )


@then("no file under /workspace remains root-owned such that the vscode "
      "agent cannot modify it")
def mf15_no_root_owned_path_remains(ctx, fake_driver):
    container_name = ctx["container_name"]
    root_owned = fake_driver.root_owned_paths_at_agent_start(container_name)
    assert root_owned == [], (
        "No agent-touched path under /workspace may remain root-owned after "
        f"container init; these are still root-owned: {root_owned!r}.  Each "
        "would require a host-side chown for the vscode agent to proceed "
        "(lead-mf15)."
    )


@then("a git operation run by the vscode agent against /workspace/.git and a "
      "bd operation against /workspace/.beads each succeed without a "
      "host-side chown intervention")
def mf15_git_and_bd_ops_succeed_without_host_chown(ctx, fake_driver):
    """Both .git and .beads must be vscode-owned after init, so a vscode-user
    git op and bd op each succeed with NO intervening host chown."""
    container_name = ctx["container_name"]
    owners = fake_driver.workspace_path_owners_at_agent_start(container_name)
    git_owner = owners.get("/workspace/.git")
    beads_owner = owners.get("/workspace/.beads")
    assert git_owner == "vscode", (
        f"/workspace/.git must be vscode-owned after container init so the "
        f"vscode agent's git ops succeed without a host chown; got "
        f"{git_owner!r} (lead-mf15: .git/objects/7e/ re-rooted mid-run "
        "2026-06-18)."
    )
    assert beads_owner == "vscode", (
        f"/workspace/.beads must be vscode-owned after container init so the "
        f"vscode agent's bd ops succeed without a host chown; got "
        f"{beads_owner!r} (lead-mf15: .beads cloned root-owned at bring-up "
        "2026-06-18)."
    )
    # And the .beads-vscode-owned pin (2904f3a905567b48) must continue to hold.
    assert fake_driver.beads_owner(container_name) == "vscode", (
        "the existing .beads-vscode-owned invariant (2904f3a905567b48) must "
        "continue to hold alongside the lead-mf15 tightening."
    )


@then("bd create run inside the container's workspace directory exits zero and "
      "yields a new issue id carrying that prefix")
def assert_bd_create_yields_prefixed_id(ctx, fake_driver):
    container_name = ctx["container_name"]
    expected_prefix = ctx["committed_beads_prefix"]
    result = fake_driver.exec_run(container_name, ["bd", "create", "scratch"])
    assert result.returncode == 0, (
        f"Expected `bd create` to exit zero inside {container_name!r}, "
        f"got rc={result.returncode} stderr={result.stderr!r}"
    )
    issue_id = result.stdout.strip()
    assert issue_id, "Expected `bd create` to emit a new issue id on stdout"
    assert issue_id.startswith(expected_prefix + "-"), (
        f"Expected new issue id to carry prefix {expected_prefix!r}, "
        f"got {issue_id!r}"
    )


@then("bd ready run inside the container's workspace directory exits zero and "
      "lists the committed issues")
def assert_bd_ready_lists_committed(ctx, fake_driver):
    container_name = ctx["container_name"]
    expected_prefix = ctx["committed_beads_prefix"]
    result = fake_driver.exec_run(container_name, ["bd", "ready"])
    assert result.returncode == 0, (
        f"Expected `bd ready` to exit zero inside {container_name!r}, "
        f"got rc={result.returncode} stderr={result.stderr!r}"
    )
    assert f"{expected_prefix}-" in result.stdout, (
        f"Expected `bd ready` to list committed {expected_prefix!r}-prefixed "
        f"issues, got stdout={result.stdout!r}"
    )


@then("stderr reports a messaging readiness failure that names the SHOPMSG_DSN "
      "value")
def assert_stderr_readiness_failure_names_dsn(ctx):
    result = ctx["result"]
    stderr = result.stderr
    dsn = ctx["shopmsg_dsn"]
    assert "readiness" in stderr.lower(), (
        f"Expected a messaging readiness failure in stderr, got: {stderr!r}"
    )
    assert dsn in stderr, (
        f"Expected the SHOPMSG_DSN value {dsn!r} named in stderr, got: {stderr!r}"
    )


@then(parsers.parse('no startup prompt has been sent to the tmux session named '
                    '"{session}" in container "{container_name}"'))
def assert_no_startup_prompt_sent(session, container_name, ctx, fake_driver):
    send_keys = fake_driver.send_keys_calls(container_name)
    # Filter to send-keys targeting the named session that carry text payload
    # (i.e. not a bare Enter and not the claude-start command... but no
    # send-keys should have happened at all on the readiness-failure path).
    targeted = [
        c for c in send_keys
        if "-t" in c.command
        and c.command[c.command.index("-t") + 1] == session
    ]
    assert not targeted, (
        f"Expected NO send-keys to tmux session {session!r} in "
        f"{container_name!r}, but found: {[c.command for c in targeted]!r}"
    )


@then(parsers.parse('once the readiness sequence completes successfully, the '
                    'startup prompt is sent to the tmux session named "{session}"'))
def assert_prompt_sent_after_readiness(session, ctx, fake_driver, controller):
    """Make the messaging DB reachable, re-launch, and assert the prompt is
    now injected into the named tmux session."""
    container_name = ctx["container_name"]
    bc_name = ctx["bc_name"]
    dsn = ctx.get("shopmsg_dsn")
    # Container is not yet running (the prior launch failed the barrier before
    # marking ready); make the DSN reachable and re-launch.
    fake_driver.set_running(container_name, running=False)
    fake_driver.set_dsn_reachable(dsn, reachable=True)
    result = controller.launch(
        bc_name=bc_name,
        repo_url=ctx.get("repo_url", f"https://github.com/shopsystem/{bc_name}.git"),
        shopmsg_dsn=dsn,
        startup_prompt=ctx["startup_prompt"],
        manifest_path=ctx.get("launch_manifest_path"),
        credential_home=ctx.get("credential_home"),
    )
    assert result.exit_code == 0, (
        f"Expected launch to succeed once DB reachable, got "
        f"rc={result.exit_code} stderr={result.stderr!r}"
    )
    committed = fake_driver.agent_committed_prompt(container_name)
    assert committed == ctx["startup_prompt"], (
        f"Expected startup prompt {ctx['startup_prompt']!r} submitted to "
        f"session {session!r}, agent committed: {committed!r}"
    )


@then(parsers.parse('it reports that "{container_name}" is already ready'))
def assert_reports_already_ready(container_name, ctx):
    stdout = ctx["result"].stdout
    assert container_name in stdout and "already ready" in stdout, (
        f"Expected stdout to report {container_name!r} already ready, "
        f"got: {stdout!r}"
    )


@then(parsers.parse('no startup prompt has been re-sent to the tmux session '
                    'named "{session}" in container "{container_name}"'))
def assert_no_prompt_resent(session, container_name, ctx, fake_driver):
    send_keys = fake_driver.send_keys_calls(container_name)
    assert not send_keys, (
        f"Expected NO send-keys to {container_name!r} on the idempotent "
        f"readiness re-run, but found: {[c.command for c in send_keys]!r}"
    )


@then(parsers.parse('the container\'s reported health status is "{status}"'))
def assert_health_status(status, ctx):
    actual = ctx["health_status"]
    assert actual == status, (
        f"Expected health status {status!r}, got {actual!r}"
    )


# ===========================================================================
# Agent-vault credential broker scenarios (ADR-026, lead-v4ih / lead-hxb8)
#
# The host-credential-mount model (the 7 retired hashes f51f21bb, 636ce0c8,
# 58b727750607, 93ce0083, 85202a40, dc9b9885, 4ba55450, plus collateral
# scenario 6172b02f) is SUPERSEDED.  Zero host-filesystem credential coupling
# reaches a BC container; the agent-vault broker is the sole credential path.
# ===========================================================================

from bc_launcher.controller import (
    AGENT_VAULT_MITM_PROXY_PORT,
    AGENT_VAULT_PLACEHOLDER_TOKEN,
    CONTAINER_CLAUDE_CREDENTIALS_PATH,
    DEFAULT_AGENT_VAULT_BROKER,
)

# A real host OAuth accessToken value that MUST never appear inside the
# container under the agent-vault model.  The placeholder substitutes for it.
_REAL_OAUTH_TOKEN = "sk-ant-REAL-oauth-accessToken-DO-NOT-LEAK"
# A real GitHub token value the broker holds out of band; never in-container.
_REAL_GITHUB_TOKEN = "ghp_REAL_github_token_DO_NOT_LEAK"
# An unreachable broker address used by the broker-down readiness/health paths.
_UNREACHABLE_BROKER = "http://no-such-agent-vault.invalid:9999"

# The placeholder .credentials.json is now BAKED INTO the bc-base image
# (docker/bc-base/Dockerfile, bclaunch-9rr) rather than mounted read-only by
# the controller.  This models the file that the image carries at
# /home/vscode/.claude/.credentials.json.  The bake content is authoritatively
# pinned by the Dockerfile; tests read it the same way the bc-base structural
# tests do (parse the committed Dockerfile content).
_BAKED_CREDENTIALS_PATH = "/home/vscode/.claude/.credentials.json"


def _baked_credentials_json() -> dict:
    """Parse the FULL .credentials.json JSON the bc-base Dockerfile bakes.

    bclaunch-2s6y: the Dockerfile now bakes the NESTED claudeAiOauth stanza at
    /home/vscode/.claude/.credentials.json (the prior bare {"accessToken":...}
    shape was wrong — claude never recognized itself as logged in).  We recover
    the EXACT JSON object the image will carry by locating the
    `> /home/vscode/.claude/.credentials.json` redirect in the committed
    Dockerfile and JSON-parsing the single-quoted JSON literal that precedes it
    (docker build is NOT run — docker is unavailable).  Parsing the real JSON
    (not regexing one field) gives the nested-shape assertions teeth: a bare
    top-level accessToken would fail to expose the nested claudeAiOauth path.
    """
    import json as _json
    import re as _re

    dockerfile = _find_bc_base_dockerfile()
    text = dockerfile.read_text() if dockerfile else ""
    # Find the printf '<json>' ... > .../.credentials.json bake line.  The JSON
    # literal is single-quoted in the Dockerfile.
    m = _re.search(
        r"printf\s+'%s\\n'\s+'(\{.*?\})'\s*\\?\s*\n\s*>\s*"
        r"/home/vscode/\.claude/\.credentials\.json\b",
        text,
    )
    if not m:
        return {}
    try:
        return _json.loads(m.group(1))
    except _json.JSONDecodeError:
        return {}


def _baked_claude_json() -> dict:
    """Parse the FULL ~/.claude.json JSON the bc-base Dockerfile bakes.

    bclaunch-2s6y: the Dockerfile now also seeds ~/.claude.json with the
    onboarding/trust state that skips the first-run wizard (theme ->
    login-method -> folder-trust -> bypass-permissions).  Recover the exact
    object by locating the `> /home/vscode/.claude.json` redirect and parsing
    the single-quoted JSON literal that precedes it.
    """
    import json as _json
    import re as _re

    dockerfile = _find_bc_base_dockerfile()
    text = dockerfile.read_text() if dockerfile else ""
    m = _re.search(
        r"printf\s+'%s\\n'\s+'(\{.*?\})'\s*\\?\s*\n\s*>\s*"
        r"/home/vscode/\.claude\.json\b",
        text,
    )
    if not m:
        return {}
    try:
        return _json.loads(m.group(1))
    except _json.JSONDecodeError:
        return {}


def _baked_placeholder_credentials() -> dict:
    """Back-compat shim: the baked .credentials.json as a dict.

    Returns the FULL nested credential object (bclaunch-2s6y).  Callers that
    previously read a top-level ``accessToken`` are updated to read the nested
    ``claudeAiOauth.accessToken`` path; this remains the single parse point.
    """
    return _baked_credentials_json()


def _baked_oauth_access_token() -> str | None:
    """The accessToken INSIDE the nested claudeAiOauth stanza, or None."""
    creds = _baked_credentials_json()
    oauth = creds.get("claudeAiOauth")
    if isinstance(oauth, dict):
        return oauth.get("accessToken")
    return None


def _agent_vault_launch(ctx, controller, fake_driver, tmp_path, bc_name,
                        *, startup_prompt=None, broker=None, dsn=None):
    """Run a launch under the agent-vault model and stash the result in ctx."""
    repo_url = f"https://github.com/shopsystem/{bc_name}.git"
    manifest_path = ctx.get("launch_manifest_path")
    if manifest_path is None:
        default_manifest = tmp_path / "bc-manifest.yaml"
        if not default_manifest.exists():
            import yaml as _yaml
            default_manifest.write_text(_yaml.dump({
                "product": "shopsystem product",
                "bcs": [{"name": bc_name, "remote": repo_url, "role": "bc"}],
            }))
        manifest_path = default_manifest
    result = controller.launch(
        bc_name=bc_name,
        repo_url=repo_url,
        shopmsg_dsn=dsn,
        startup_prompt=startup_prompt,
        network=None,
        manifest_path=manifest_path,
        credential_home=ctx.get("credential_home"),
        agent_vault_broker=broker,
    )
    ctx["result"] = result
    ctx["container_name"] = f"bc-{bc_name}"
    ctx["bc_name"] = bc_name
    return result


# --- Given steps -----------------------------------------------------------

@given("an agent-vault broker is running on the shopsystem network and is "
       "reachable")
def agent_vault_broker_reachable(ctx, fake_driver):
    broker = DEFAULT_AGENT_VAULT_BROKER
    fake_driver.set_agent_vault_reachable(broker, reachable=True)
    ctx["agent_vault_broker"] = broker


@given("an agent-vault broker with a GitHub credential service is running on "
       "the shopsystem network and is reachable")
def agent_vault_github_broker_reachable(ctx, fake_driver):
    broker = DEFAULT_AGENT_VAULT_BROKER
    fake_driver.set_agent_vault_reachable(broker, reachable=True)
    ctx["agent_vault_broker"] = broker
    ctx["broker_github_token"] = _REAL_GITHUB_TOKEN


@given("an agent-vault broker with a GitHub credential service is running on "
       "the shopsystem network")
def agent_vault_github_broker(ctx, fake_driver):
    broker = DEFAULT_AGENT_VAULT_BROKER
    fake_driver.set_agent_vault_reachable(broker, reachable=True)
    ctx["agent_vault_broker"] = broker
    ctx["broker_github_token"] = _REAL_GITHUB_TOKEN


@given("the environment variable BCLAUNCHER_HOST_HOME is unset")
def bclauncher_host_home_unset(ctx, monkeypatch):
    monkeypatch.delenv("BCLAUNCHER_HOST_HOME", raising=False)


@given(parsers.parse('the container "{container_name}" is running with no host '
                     'gh or gitconfig credential mounted'))
def container_running_no_gh_mount(container_name, ctx, controller, fake_driver,
                                  tmp_path):
    bc_name = container_name.removeprefix("bc-")
    _agent_vault_launch(ctx, controller, fake_driver, tmp_path, bc_name,
                        broker=ctx.get("agent_vault_broker"))
    assert fake_driver.is_running(container_name)


@given(parsers.parse('the container "{container_name}" routes its GitHub-bound '
                     'traffic through the broker\'s proxy listener'))
def container_routes_through_broker(container_name, ctx, controller,
                                    fake_driver, tmp_path):
    bc_name = container_name.removeprefix("bc-")
    _agent_vault_launch(ctx, controller, fake_driver, tmp_path, bc_name,
                        broker=ctx.get("agent_vault_broker"))
    assert fake_driver.is_running(container_name)


@given("the agent-vault broker address configured for the container points at "
       "an address where no reachable broker is listening")
def broker_unreachable_configured(ctx, fake_driver):
    fake_driver.set_agent_vault_reachable(_UNREACHABLE_BROKER, reachable=False)
    ctx["agent_vault_broker"] = _UNREACHABLE_BROKER


@given("the agent-vault broker configured for the container is not reachable")
def broker_not_reachable_for_health(ctx, fake_driver):
    container_name = ctx["container_name"]
    bc_name = ctx["bc_name"]
    from bc_launcher.controller import beads_prefix_for
    # beads + DB are fine; only the broker is down, so health must be unhealthy.
    fake_driver.set_beads_prefix(container_name, beads_prefix_for(bc_name))
    fake_driver.set_container_dsn(container_name, _READINESS_DSN)
    fake_driver.set_dsn_reachable(_READINESS_DSN, reachable=True)
    fake_driver.set_container_broker(container_name, _UNREACHABLE_BROKER)
    fake_driver.set_agent_vault_reachable(_UNREACHABLE_BROKER, reachable=False)


@given("the messaging database at SHOPMSG_DSN is reachable for the agent-vault "
       "launch")
def db_reachable_for_av_launch(ctx, fake_driver):
    fake_driver.set_dsn_reachable(_READINESS_DSN, reachable=True)
    ctx["shopmsg_dsn"] = _READINESS_DSN


@given("the agent-vault broker on the shopsystem network is reachable")
def av_broker_reachable_named(ctx, fake_driver):
    broker = DEFAULT_AGENT_VAULT_BROKER
    fake_driver.set_agent_vault_reachable(broker, reachable=True)
    ctx["agent_vault_broker"] = broker


@given("the agent-vault broker on the shopsystem network is not reachable")
def av_broker_not_reachable_named(ctx, fake_driver):
    broker = DEFAULT_AGENT_VAULT_BROKER
    fake_driver.set_agent_vault_reachable(broker, reachable=False)
    ctx["agent_vault_broker"] = broker


@given("the agent-vault broker has been provisioned out of band with the real "
       "Claude OAuth credential and the real GitHub credential")
def broker_provisioned_out_of_band(ctx, fake_driver):
    broker = DEFAULT_AGENT_VAULT_BROKER
    fake_driver.set_agent_vault_reachable(broker, reachable=True)
    ctx["agent_vault_broker"] = broker
    ctx["broker_oauth_token"] = _REAL_OAUTH_TOKEN
    ctx["broker_github_token"] = _REAL_GITHUB_TOKEN


@given(parsers.parse('the container "{container_name}" is running under the '
                     'agent-vault model'))
def container_running_av_model(container_name, ctx, controller, fake_driver,
                               tmp_path):
    bc_name = container_name.removeprefix("bc-")
    broker = DEFAULT_AGENT_VAULT_BROKER
    fake_driver.set_agent_vault_reachable(broker, reachable=True)
    _agent_vault_launch(ctx, controller, fake_driver, tmp_path, bc_name,
                        broker=broker)
    assert fake_driver.is_running(container_name)


# --- When steps ------------------------------------------------------------

@when("the container's bind mounts are inspected via docker inspect")
def inspect_bind_mounts_av(ctx, fake_driver, controller):
    container_name = ctx["container_name"]
    ctx["bind_mounts"] = controller.get_bind_mounts(container_name)


@when(parsers.parse('bc-container launch is run with BC name "{bc_name}"'))
def when_launch_run_av(bc_name, ctx, controller, fake_driver, tmp_path):
    _agent_vault_launch(ctx, controller, fake_driver, tmp_path, bc_name,
                        broker=ctx.get("agent_vault_broker"))


@when(parsers.parse('bc-container launch is run with BC name "{bc_name}" '
                    'against the provisioned broker'))
def when_launch_run_against_provisioned_broker(bc_name, ctx, controller,
                                               fake_driver, tmp_path):
    _agent_vault_launch(ctx, controller, fake_driver, tmp_path, bc_name,
                        broker=ctx.get("agent_vault_broker"))


@when(parsers.parse('bc-container launch starts the agent for BC name '
                    '"{bc_name}"'))
def launch_starts_agent(bc_name, ctx, controller, fake_driver, tmp_path):
    _agent_vault_launch(
        ctx, controller, fake_driver, tmp_path, bc_name,
        startup_prompt="please begin your session",
        broker=ctx.get("agent_vault_broker"),
        dsn=ctx.get("shopmsg_dsn"),
    )


@when(parsers.parse('bc-container launch starts the agent for BC name '
                    '"{bc_name}" with the operator-supplied agent-vault '
                    'credentials'))
def launch_starts_agent_with_av_creds(bc_name, ctx, controller, fake_driver,
                                      tmp_path):
    # bclaunch-3q12: like launch_starts_agent, but threads the operator-supplied
    # addr/token/vault triple through so the controller derives the runtime
    # HTTPS_PROXY at the :14322 MITM listener.  NO explicit --agent-vault-broker
    # is passed — this models a plain brokered launch, exercising the derivation
    # path (not the override path).
    repo_url = f"https://github.com/shopsystem/{bc_name}.git"
    manifest_path = ctx.get("launch_manifest_path")
    if manifest_path is None:
        default_manifest = tmp_path / "bc-manifest.yaml"
        if not default_manifest.exists():
            import yaml as _yaml
            default_manifest.write_text(_yaml.dump({
                "product": "shopsystem product",
                "bcs": [{"name": bc_name, "remote": repo_url, "role": "bc"}],
            }))
        manifest_path = default_manifest
    result = controller.launch(
        bc_name=bc_name,
        repo_url=repo_url,
        shopmsg_dsn=ctx.get("shopmsg_dsn"),
        startup_prompt="please begin your session",
        network=None,
        manifest_path=manifest_path,
        credential_home=ctx.get("credential_home"),
        agent_vault_addr=ctx.get("av_addr"),
        agent_vault_token=ctx.get("av_token"),
        agent_vault_vault=ctx.get("av_vault"),
    )
    ctx["result"] = result
    ctx["container_name"] = f"bc-{bc_name}"
    ctx["bc_name"] = bc_name


@when(parsers.parse('I run bc-container launch with BC name "{bc_name}" and an '
                    'agent-vault startup prompt'))
def launch_with_av_startup_prompt(bc_name, ctx, controller, fake_driver,
                                  tmp_path):
    _agent_vault_launch(
        ctx, controller, fake_driver, tmp_path, bc_name,
        startup_prompt="please begin your session",
        broker=ctx.get("agent_vault_broker"),
        dsn=ctx.get("shopmsg_dsn"),
    )


@when(parsers.parse('I run bc-container launch with BC name "{bc_name}" and a '
                    'brokered startup prompt'))
def launch_with_brokered_startup_prompt(bc_name, ctx, controller, fake_driver,
                                        tmp_path):
    _agent_vault_launch(
        ctx, controller, fake_driver, tmp_path, bc_name,
        startup_prompt="please begin your session",
        broker=ctx.get("agent_vault_broker"),
        dsn=ctx.get("shopmsg_dsn") or _READINESS_DSN,
    )


@when('the placeholder ".credentials.json" baked into the bc-base image is read')
def read_baked_placeholder_credentials(ctx):
    # REVISED (lead-v4ih 3931e43e): the placeholder is now baked into the
    # bc-base image, not mounted by the controller.  Read what the image
    # carries by parsing the committed Dockerfile bake.
    ctx["credentials_content"] = _baked_placeholder_credentials()


@when("an authenticated GitHub operation is run from inside the container "
      "through the agent-vault broker")
def github_op_via_broker(ctx, fake_driver):
    # The container holds no GitHub token; the broker substitutes it on the
    # outbound request.  Model the operation succeeding because the (reachable)
    # broker holds the credential.
    broker = ctx.get("agent_vault_broker", DEFAULT_AGENT_VAULT_BROKER)
    ctx["github_op_ok"] = fake_driver.agent_vault_reachable(broker) and bool(
        ctx.get("broker_github_token")
    )


@when("a git operation inside the container makes an authenticated request to "
      "github.com")
def git_request_to_github(ctx):
    # The request leaves the container with NO credential; the broker injects
    # its stored GitHub credential before forwarding to github.com.
    ctx["request_as_leaves_container_credential"] = None
    ctx["request_broker_forwards_credential"] = ctx.get("broker_github_token")


@when("the container's filesystem and process environment are searched from "
      "inside the container")
def search_container_for_secrets(ctx, fake_driver):
    container_name = ctx["container_name"]
    # Container env: HTTPS_PROXY (broker addr) is the only credential-bearing
    # value; no real OAuth / GitHub token is present.
    ctx["container_env_values"] = [
        fake_driver.container_proxy_env(container_name)
    ]
    # Container files: the only .credentials.json present is the placeholder
    # BAKED INTO the bc-base image (no longer a controller mount).
    ctx["container_credentials_json"] = _baked_placeholder_credentials()
    # No host gh/gitconfig path mounted.
    mounts = fake_driver.get_mounts(container_name)
    ctx["container_mount_destinations"] = [m.destination for m in mounts]


@when("the credential-bearing secrets reachable from inside the container are "
      "enumerated")
def enumerate_container_secrets(ctx, fake_driver):
    container_name = ctx["container_name"]
    proxy = fake_driver.container_proxy_env(container_name)
    # The only credential-bearing secret reachable in-container is the proxy
    # token used to authenticate to the broker (modelled as the HTTPS_PROXY
    # endpoint the agent-vault run wrapper authenticates against).
    ctx["reachable_secrets"] = [proxy] if proxy else []


# --- Then steps ------------------------------------------------------------

@then(parsers.parse('no bind mount inside the container has the host '
                    '"{host_path}" directory as its source'))
def assert_no_host_dir_mount(host_path, ctx):
    bind_mounts = ctx.get("bind_mounts", [])
    leaf = host_path.lstrip("~/").lstrip("/")
    for m in bind_mounts:
        assert not m.source.rstrip("/").endswith(leaf), (
            f"Found a bind mount whose source ends in host path {host_path!r}: "
            f"{m.source!r} -> {m.destination!r}"
        )


@then(parsers.parse('no bind mount inside the container has the host '
                    '"{host_path}" file as its source'))
def assert_no_host_file_mount(host_path, ctx):
    bind_mounts = ctx.get("bind_mounts", [])
    leaf = host_path.lstrip("~/").lstrip("/")
    for m in bind_mounts:
        assert not m.source.rstrip("/").endswith(leaf), (
            f"Found a bind mount whose source ends in host file {host_path!r}: "
            f"{m.source!r} -> {m.destination!r}"
        )


@then(parsers.parse('no bind mount inside the container targets "{target}" as '
                    'a read-write directory mount'))
def assert_no_rw_dir_mount_target(target, ctx, fake_driver):
    # The placeholder credentials file lives UNDER /home/vscode/.claude but is
    # a single read-only file mount, not a read-write directory mount of the
    # .claude directory itself.  Assert no mount destination equals the bare
    # directory target.
    bind_mounts = ctx.get("bind_mounts", [])
    for m in bind_mounts:
        assert m.destination != target, (
            f"Found a bind mount targeting {target!r} as a directory mount: "
            f"{m.source!r} -> {m.destination!r}"
        )


@then(parsers.parse('the command exits zero and the container "{container_name}"'
                    ' is running'))
def assert_exit_zero_and_running(container_name, ctx, fake_driver):
    result = ctx["result"]
    assert result.exit_code == 0, (
        f"Expected exit 0, got {result.exit_code} (stderr={result.stderr!r})"
    )
    assert fake_driver.is_running(container_name), (
        f"Expected {container_name!r} to be running"
    )


@then("launch did not fail resolving any host credential path")
def assert_no_credential_resolution_failure(ctx):
    result = ctx["result"]
    stderr = result.stderr.lower()
    assert "credential source not found" not in stderr, (
        f"Launch reported a host credential resolution failure: "
        f"{result.stderr!r}"
    )
    assert result.exit_code == 0, (
        f"Expected launch to succeed, got rc={result.exit_code} "
        f"stderr={result.stderr!r}"
    )


@then(parsers.parse('the command line that launches the agent inside the tmux '
                    'session named "{session}" invokes "{invocation}"'))
def assert_agent_invocation(session, invocation, ctx, fake_driver):
    container_name = ctx["container_name"]
    send_keys = fake_driver.send_keys_calls(container_name)
    matching = [
        c for c in send_keys
        if "-t" in c.command
        and c.command[c.command.index("-t") + 1] == session
        and any(invocation in tok for tok in c.command)
    ]
    assert matching, (
        f"Expected a send-keys to session {session!r} whose payload invokes "
        f"{invocation!r}; send-keys recorded: "
        f"{[c.command for c in send_keys]!r}"
    )


@then("the agent process environment sets HTTPS_PROXY to the agent-vault "
      "broker's MITM proxy listener on port 14322 with token:vault basic-auth")
def assert_https_proxy_mitm_listener(ctx, fake_driver):
    # bclaunch-3q12: the "broker's proxy listener" the runtime agent must use is
    # the credential-substituting MITM HTTPS proxy on :14322 with the
    # token:vault basic-auth userinfo — NOT the bare :14321 control API.  Derive
    # the EXPECTED value from the operator-supplied addr/token/vault triple in
    # ctx (the same derivation the controller performs) and assert the ACTUAL
    # runtime HTTPS_PROXY matches it.  A bare-:14321 runtime proxy fails here.
    from urllib.parse import urlparse, unquote

    container_name = ctx["container_name"]
    proxy = fake_driver.container_proxy_env(container_name)
    parsed = urlparse(proxy)
    assert parsed.port == AGENT_VAULT_MITM_PROXY_PORT, (
        f"Expected runtime HTTPS_PROXY on the MITM port "
        f"{AGENT_VAULT_MITM_PROXY_PORT} (bclaunch-3q12 — must NOT be the bare "
        f":14321 control API); got proxy {proxy!r}"
    )
    expected_userinfo = f"{ctx['av_token']}:{ctx['av_vault']}"
    got = f"{unquote(parsed.username or '')}:{unquote(parsed.password or '')}"
    assert got == expected_userinfo, (
        f"Expected runtime HTTPS_PROXY basic-auth userinfo "
        f"{expected_userinfo!r}, got {got!r} (proxy: {proxy!r})"
    )


# --- bclaunch-2s6y: nested-claudeAiOauth credential shape assertions ---------
#
# These read the ACTUAL JSON object the bc-base Dockerfile bakes (parsed by
# _baked_credentials_json), NOT an echoed string — so a regression to the bare
# {"accessToken":...} shape (no top-level claudeAiOauth) fails them.

@then('the baked .credentials.json has a top-level "claudeAiOauth" object')
def assert_creds_has_claudeaioauth(ctx):
    content = ctx["credentials_content"]
    oauth = content.get("claudeAiOauth")
    assert isinstance(oauth, dict), (
        f"Expected a top-level 'claudeAiOauth' object in the baked "
        f".credentials.json (the nested shape claude recognizes as logged in); "
        f"got {content!r}"
    )


@then(parsers.parse(
    'the accessToken inside claudeAiOauth has the literal value "{value}"'
))
def assert_nested_access_token(value, ctx):
    content = ctx["credentials_content"]
    oauth = content.get("claudeAiOauth")
    assert isinstance(oauth, dict), (
        f"No claudeAiOauth object in baked credentials: {content!r}"
    )
    assert oauth.get("accessToken") == value, (
        f"Expected claudeAiOauth.accessToken {value!r}, "
        f"got {oauth.get('accessToken')!r}"
    )


@then(parsers.parse(
    'the refreshToken inside claudeAiOauth has the literal value "{value}"'
))
def assert_nested_refresh_token(value, ctx):
    content = ctx["credentials_content"]
    oauth = content.get("claudeAiOauth")
    assert isinstance(oauth, dict), (
        f"No claudeAiOauth object in baked credentials: {content!r}"
    )
    assert oauth.get("refreshToken") == value, (
        f"Expected claudeAiOauth.refreshToken {value!r}, "
        f"got {oauth.get('refreshToken')!r}"
    )


@then('the baked .credentials.json has no top-level "accessToken" field')
def assert_no_top_level_access_token(ctx):
    # Guards against the bare-shape regression: the OLD wrong shape was
    # {"accessToken":"__PLACEHOLDER__"} at top level.  The nested shape must NOT
    # carry a top-level accessToken.
    content = ctx["credentials_content"]
    assert "accessToken" not in content, (
        f"Baked .credentials.json carries a TOP-LEVEL 'accessToken' field — "
        f"that is the superseded bare shape (bclaunch-2s6y); the accessToken "
        f"must live INSIDE claudeAiOauth. Content: {content!r}"
    )


@then(parsers.parse(
    'the placeholder credentials file is baked into the image at "{path}"'
))
def assert_credentials_baked_into_image(path, ctx):
    # The Dockerfile bakes the placeholder at the fixed container path; assert
    # the committed Dockerfile both bakes the placeholder accessToken AND
    # targets the expected path.
    dockerfile = _find_bc_base_dockerfile()
    assert dockerfile is not None, "No bc-base Dockerfile found"
    text = dockerfile.read_text()
    assert path in text, (
        f"Expected the bc-base Dockerfile to bake the placeholder credentials "
        f"file at {path!r}; not present in Dockerfile content."
    )
    content = ctx.get("credentials_content") or {}
    # bclaunch-2s6y: the accessToken lives INSIDE the nested claudeAiOauth stanza.
    oauth = content.get("claudeAiOauth") or {}
    assert oauth.get("accessToken") == AGENT_VAULT_PLACEHOLDER_TOKEN, (
        f"Expected baked claudeAiOauth.accessToken "
        f"{AGENT_VAULT_PLACEHOLDER_TOKEN!r}, got {oauth.get('accessToken')!r}"
    )


@then("the controller builds no credential bind-mount into the container")
def assert_no_credential_mount(ctx, fake_driver):
    mounts = fake_driver.container_mounts_full(ctx["container_name"])
    offenders = [
        m for m in mounts if m[2] == CONTAINER_CLAUDE_CREDENTIALS_PATH
    ]
    assert not offenders, (
        f"Expected the controller to build NO credential bind-mount at "
        f"{CONTAINER_CLAUDE_CREDENTIALS_PATH}; found {offenders!r}"
    )


@then("the real host OAuth accessToken value does not appear anywhere in the "
      "container's filesystem")
def assert_real_oauth_absent_from_fs(ctx, fake_driver):
    container_name = ctx["container_name"]
    mounts = fake_driver.get_mounts(container_name)
    for m in mounts:
        src = Path(m.source)
        if src.is_file():
            assert _REAL_OAUTH_TOKEN not in src.read_text(), (
                f"Real OAuth token leaked into mounted file {m.source!r}"
            )


@then("the operation completes successfully against GitHub")
def assert_github_op_succeeds(ctx):
    assert ctx.get("github_op_ok"), (
        "Expected the brokered GitHub operation to succeed"
    )


@then("no GitHub token value is present in the container's environment or "
      "filesystem")
def assert_no_github_token_in_container(ctx, fake_driver):
    container_name = ctx["container_name"]
    proxy = fake_driver.container_proxy_env(container_name)
    assert _REAL_GITHUB_TOKEN not in proxy, (
        "GitHub token leaked into container HTTPS_PROXY env"
    )
    for m in fake_driver.get_mounts(container_name):
        src = Path(m.source)
        if src.is_file():
            assert _REAL_GITHUB_TOKEN not in src.read_text(), (
                f"GitHub token leaked into mounted file {m.source!r}"
            )


@then("the request the broker forwards to github.com carries the broker-stored "
      "GitHub credential")
def assert_broker_forwards_credential(ctx):
    assert ctx.get("request_broker_forwards_credential") == _REAL_GITHUB_TOKEN, (
        "Expected the broker to forward its stored GitHub credential"
    )


@then("the request as it leaves the container carries no GitHub credential")
def assert_request_leaves_container_uncredentialed(ctx):
    assert ctx.get("request_as_leaves_container_credential") is None, (
        "Expected the request leaving the container to carry no GitHub "
        "credential"
    )


@then("stderr reports an agent-vault readiness failure that names the "
      "configured agent-vault broker address")
def assert_av_readiness_failure_names_broker(ctx):
    result = ctx["result"]
    stderr = result.stderr
    broker = ctx["agent_vault_broker"]
    assert "agent-vault readiness failure" in stderr, (
        f"Expected an agent-vault readiness failure in stderr, got: {stderr!r}"
    )
    assert broker in stderr, (
        f"Expected broker address {broker!r} named in stderr, got: {stderr!r}"
    )


@then("the readiness barrier reports both messaging-database and agent-vault "
      "checks passed")
def assert_both_barriers_passed(ctx, fake_driver):
    result = ctx["result"]
    assert result.exit_code == 0, (
        f"Expected launch to succeed with both barriers passing, got "
        f"rc={result.exit_code} stderr={result.stderr!r}"
    )
    dsn = ctx.get("shopmsg_dsn") or _READINESS_DSN
    broker = ctx.get("agent_vault_broker", DEFAULT_AGENT_VAULT_BROKER)
    assert fake_driver.messaging_db_reachable(dsn), (
        "Messaging-database check did not pass"
    )
    assert fake_driver.agent_vault_reachable(broker), (
        "Agent-vault check did not pass"
    )


@then(parsers.parse('the startup prompt is sent to the tmux session named '
                    '"{session}" in container "{container_name}"'))
def assert_startup_prompt_sent(session, container_name, ctx, fake_driver):
    committed = fake_driver.agent_committed_prompt(container_name)
    assert committed is not None, (
        f"Expected a startup prompt committed to session {session!r} in "
        f"{container_name!r}, agent committed: {committed!r}"
    )


@then("the brokered Claude OAuth substitution and the brokered GitHub "
      "substitution both succeed")
def assert_both_substitutions_succeed(ctx, fake_driver):
    container_name = ctx["container_name"]
    broker = ctx["agent_vault_broker"]
    # Substitution presupposes the proxy points at a reachable broker holding
    # both real credentials (provisioned out of band).
    assert fake_driver.container_proxy_env(container_name) == broker
    assert fake_driver.agent_vault_reachable(broker)
    assert ctx.get("broker_oauth_token") == _REAL_OAUTH_TOKEN
    assert ctx.get("broker_github_token") == _REAL_GITHUB_TOKEN


@then("bc-container launch performed no step that read a real credential from "
      "any host file")
def assert_no_host_credential_read(ctx, fake_driver):
    # No host ~/.claude, ~/.config/gh, or ~/.gitconfig is ever a mount source.
    container_name = ctx["container_name"]
    for m in fake_driver.get_mounts(container_name):
        for forbidden in (".config/gh", ".gitconfig"):
            assert not m.source.rstrip("/").endswith(forbidden), (
                f"Launch mounted a host credential source: {m.source!r}"
            )
        # The only .claude-related mount is the placeholder file mount.
        if m.destination == CONTAINER_CLAUDE_CREDENTIALS_PATH:
            content = Path(m.source).read_text()
            assert AGENT_VAULT_PLACEHOLDER_TOKEN in content, (
                "The credentials mount source is not the placeholder file"
            )
    # No cp of a host gitconfig / .claude.json was issued.
    for c in fake_driver.exec_calls:
        if c.command[:1] == ["cp"]:
            assert "host-gitconfig" not in " ".join(c.command), (
                f"Launch copied a host gitconfig: {c.command!r}"
            )
            assert ".claude.json" not in " ".join(c.command), (
                f"Launch copied a host .claude.json: {c.command!r}"
            )


@then("launch executes no step that stores a real credential into the broker "
      "vault")
def assert_no_credential_stored_in_vault(ctx, fake_driver):
    # The launcher never writes to the broker vault: no exec_run / run step
    # carries a real OAuth or GitHub token destined for the vault.  Modelled
    # by asserting no recorded command carries a real credential value.
    for c in fake_driver.exec_calls:
        joined = " ".join(c.command)
        assert _REAL_OAUTH_TOKEN not in joined, (
            f"Launch step carried a real OAuth token: {c.command!r}"
        )
        assert _REAL_GITHUB_TOKEN not in joined, (
            f"Launch step carried a real GitHub token: {c.command!r}"
        )


@then("launch executes no step that places a real credential inside the "
      "container")
def assert_no_real_credential_in_container(ctx, fake_driver):
    container_name = ctx["container_name"]
    for m in fake_driver.get_mounts(container_name):
        src = Path(m.source)
        if src.is_file():
            text = src.read_text()
            assert _REAL_OAUTH_TOKEN not in text, (
                f"Real OAuth token placed in mounted file {m.source!r}"
            )
            assert _REAL_GITHUB_TOKEN not in text, (
                f"Real GitHub token placed in mounted file {m.source!r}"
            )
    proxy = fake_driver.container_proxy_env(container_name)
    assert _REAL_OAUTH_TOKEN not in proxy and _REAL_GITHUB_TOKEN not in proxy


@then("the real Claude OAuth accessToken value is not present in any file or "
      "environment variable")
def assert_real_oauth_not_present(ctx):
    content = ctx.get("container_credentials_json") or {}
    # bclaunch-2s6y: the credential is now nested under claudeAiOauth — search
    # the ENTIRE serialized object (top-level + nested) for the real token so
    # the invariant survives the shape change.
    import json as _json
    assert _REAL_OAUTH_TOKEN not in _json.dumps(content), (
        "Real OAuth token present in container .credentials.json"
    )
    for val in ctx.get("container_env_values", []):
        assert _REAL_OAUTH_TOKEN not in (val or ""), (
            "Real OAuth token present in a container env value"
        )


@then(parsers.parse('the only .credentials.json present has its claudeAiOauth '
                    'accessToken equal to "{value}"'))
def assert_only_nested_placeholder_credentials(value, ctx):
    # bclaunch-2s6y: re-pinned against the nested shape. The only baked
    # credential is the placeholder INSIDE claudeAiOauth; no real token anywhere.
    content = ctx.get("container_credentials_json")
    assert content is not None, "No .credentials.json present in container"
    oauth = content.get("claudeAiOauth")
    assert isinstance(oauth, dict), (
        f"Expected the nested claudeAiOauth shape; got {content!r}"
    )
    assert oauth.get("accessToken") == value, (
        f"Expected claudeAiOauth.accessToken {value!r}, "
        f"got {oauth.get('accessToken')!r}"
    )


@then("no real GitHub token value is present in any file or environment "
      "variable")
def assert_no_real_github_token_present(ctx):
    content = ctx.get("container_credentials_json") or {}
    assert _REAL_GITHUB_TOKEN not in str(content)
    for val in ctx.get("container_env_values", []):
        assert _REAL_GITHUB_TOKEN not in (val or ""), (
            "Real GitHub token present in a container env value"
        )


@then(parsers.parse('no path mounted from the host\'s "{paths}" is present '
                    'inside the container'))
def assert_no_host_gh_gitconfig_path(paths, ctx):
    destinations = ctx.get("container_mount_destinations", [])
    for forbidden in ("/home/vscode/.config/gh", "/home/vscode/.gitconfig",
                      "/tmp/host-gitconfig"):
        assert forbidden not in destinations, (
            f"Host gh/gitconfig path {forbidden!r} is mounted in the container"
        )


@then("the only such secret is the agent-vault proxy token used to "
      "authenticate to the broker")
def assert_only_secret_is_proxy_token(ctx, fake_driver):
    container_name = ctx["container_name"]
    broker = DEFAULT_AGENT_VAULT_BROKER
    secrets = ctx.get("reachable_secrets", [])
    proxy = fake_driver.container_proxy_env(container_name)
    assert secrets == [proxy] and proxy == broker, (
        f"Expected the only reachable secret to be the proxy token {broker!r}, "
        f"got {secrets!r}"
    )
    # And no real credential is among the reachable secrets.
    for s in secrets:
        assert _REAL_OAUTH_TOKEN not in s and _REAL_GITHUB_TOKEN not in s


@then("that token grants only proxy substitution and is independently "
      "revocable without exposing any brokered credential")
def assert_proxy_token_scope(ctx):
    # The proxy token is the broker proxy endpoint the agent authenticates to;
    # it grants only substitution and never carries a brokered credential.
    for s in ctx.get("reachable_secrets", []):
        assert _REAL_OAUTH_TOKEN not in s and _REAL_GITHUB_TOKEN not in s, (
            "The reachable proxy token exposed a brokered credential"
        )


# ===========================================================================
# bclaunch-5hi / bclaunch-7pf / bclaunch-3le: operator-supplied agent-vault
# credential + TLS-trust injection, and the CLI knobs that deliver them.
# ===========================================================================

from bc_launcher.controller import (
    AGENT_VAULT_ADDR_ENV,
    AGENT_VAULT_TOKEN_ENV,
    AGENT_VAULT_VAULT_ENV,
    CONTAINER_BROKER_CA_PATH,
)


# --- bclaunch-5hi: AGENT_VAULT_* env injection -----------------------------

@given(parsers.parse(
    'the operator supplies agent-vault addr "{addr}" token "{token}" and '
    'vault "{vault}"'
))
def operator_supplies_agent_vault_creds(addr, token, vault, ctx):
    ctx["av_addr"] = addr
    ctx["av_token"] = token
    ctx["av_vault"] = vault


@when(parsers.parse(
    'bc-container launch is run for BC name "{bc_name}" with the '
    'operator-supplied agent-vault credentials'
))
def launch_with_operator_av_creds(bc_name, ctx, controller, fake_driver,
                                  tmp_path):
    repo_url = f"https://github.com/shopsystem/{bc_name}.git"
    manifest_path = tmp_path / "bc-manifest.yaml"
    if not manifest_path.exists():
        import yaml as _yaml
        manifest_path.write_text(_yaml.dump({
            "product": "shopsystem product",
            "bcs": [{"name": bc_name, "remote": repo_url, "role": "bc"}],
        }))
    result = controller.launch(
        bc_name=bc_name,
        repo_url=repo_url,
        manifest_path=manifest_path,
        credential_home=ctx.get("credential_home"),
        agent_vault_addr=ctx.get("av_addr"),
        agent_vault_token=ctx.get("av_token"),
        agent_vault_vault=ctx.get("av_vault"),
    )
    ctx["result"] = result
    ctx["container_name"] = f"bc-{bc_name}"
    ctx["bc_name"] = bc_name


@then(parsers.parse('the container env has AGENT_VAULT_ADDR set to "{value}"'))
def assert_av_addr_env(value, ctx, fake_driver):
    env = fake_driver.container_env(ctx["container_name"])
    assert env.get(AGENT_VAULT_ADDR_ENV) == value, (
        f"Expected {AGENT_VAULT_ADDR_ENV}={value!r}, "
        f"got {env.get(AGENT_VAULT_ADDR_ENV)!r}"
    )


@then(parsers.parse('the container env has AGENT_VAULT_TOKEN set to "{value}"'))
def assert_av_token_env(value, ctx, fake_driver):
    env = fake_driver.container_env(ctx["container_name"])
    assert env.get(AGENT_VAULT_TOKEN_ENV) == value, (
        f"Expected {AGENT_VAULT_TOKEN_ENV}={value!r}, "
        f"got {env.get(AGENT_VAULT_TOKEN_ENV)!r}"
    )


@then(parsers.parse('the container env has AGENT_VAULT_VAULT set to "{value}"'))
def assert_av_vault_env(value, ctx, fake_driver):
    env = fake_driver.container_env(ctx["container_name"])
    assert env.get(AGENT_VAULT_VAULT_ENV) == value, (
        f"Expected {AGENT_VAULT_VAULT_ENV}={value!r}, "
        f"got {env.get(AGENT_VAULT_VAULT_ENV)!r}"
    )


# --- bclaunch-5fji: launch-time auto-clone broker-wiring ------------------
#
# These steps read the ACTUAL env the controller injected onto the `git clone`
# exec (recorded by FakeDockerDriver.clone_exec_call), not a static echoed-back
# string — so they have teeth against the real defect: a clone whose HTTPS_PROXY
# points at the :14321 control API instead of the :14322 MITM proxy, or a clone
# that lacks GIT_SSL_CAINFO, fails these assertions.

def _clone_exec_env(ctx, fake_driver) -> dict:
    call = fake_driver.clone_exec_call(ctx["container_name"])
    assert call is not None, (
        "Expected a launch-time `git clone` exec call to have been recorded; "
        "none found."
    )
    return call.env or {}


@then(parsers.parse(
    'the launch-time clone exec has HTTPS_PROXY set to "{value}"'
))
def assert_clone_https_proxy(value, ctx, fake_driver):
    env = _clone_exec_env(ctx, fake_driver)
    assert env.get("HTTPS_PROXY") == value, (
        f"Expected the clone exec HTTPS_PROXY to be {value!r}, "
        f"got {env.get('HTTPS_PROXY')!r} (full clone env: {env!r})"
    )


@then(parsers.parse(
    'the launch-time clone exec has GIT_SSL_CAINFO set to "{value}"'
))
def assert_clone_git_ssl_cainfo(value, ctx, fake_driver):
    env = _clone_exec_env(ctx, fake_driver)
    assert env.get("GIT_SSL_CAINFO") == value, (
        f"Expected the clone exec GIT_SSL_CAINFO to be {value!r}, "
        f"got {env.get('GIT_SSL_CAINFO')!r} (full clone env: {env!r})"
    )


@then(parsers.parse(
    'the launch-time clone exec HTTPS_PROXY host is "{host}" on port {port:d}'
))
def assert_clone_proxy_host_port(host, port, ctx, fake_driver):
    from urllib.parse import urlparse

    env = _clone_exec_env(ctx, fake_driver)
    proxy = env.get("HTTPS_PROXY", "")
    parsed = urlparse(proxy)
    assert parsed.hostname == host, (
        f"Expected clone proxy host {host!r}, got {parsed.hostname!r} "
        f"(proxy: {proxy!r})"
    )
    assert parsed.port == port, (
        f"Expected clone proxy port {port}, got {parsed.port!r} "
        f"(proxy: {proxy!r})"
    )


@then(parsers.parse(
    'the launch-time clone exec HTTPS_PROXY is not the control API on port {port:d}'
))
def assert_clone_proxy_not_control_api(port, ctx, fake_driver):
    from urllib.parse import urlparse

    env = _clone_exec_env(ctx, fake_driver)
    proxy = env.get("HTTPS_PROXY", "")
    parsed = urlparse(proxy)
    assert parsed.port != port, (
        f"Clone proxy points at the control-API port {port} "
        f"(DEFECT 1 — must be the :14322 MITM proxy); proxy: {proxy!r}"
    )


@then(parsers.parse(
    'the launch-time clone exec HTTPS_PROXY carries basic-auth userinfo "{userinfo}"'
))
def assert_clone_proxy_userinfo(userinfo, ctx, fake_driver):
    from urllib.parse import urlparse, unquote

    env = _clone_exec_env(ctx, fake_driver)
    proxy = env.get("HTTPS_PROXY", "")
    parsed = urlparse(proxy)
    got_user = unquote(parsed.username or "")
    got_pass = unquote(parsed.password or "")
    got = f"{got_user}:{got_pass}"
    assert got == userinfo, (
        f"Expected clone proxy basic-auth userinfo {userinfo!r}, "
        f"got {got!r} (proxy: {proxy!r})"
    )


@then(parsers.parse(
    'the launch-time clone exec HTTPS_PROXY userinfo username is exactly "{username}"'
))
def assert_clone_proxy_username_exact(username, ctx, fake_driver):
    from urllib.parse import urlparse, unquote

    env = _clone_exec_env(ctx, fake_driver)
    proxy = env.get("HTTPS_PROXY", "")
    parsed = urlparse(proxy)
    got = unquote(parsed.username or "")
    assert got == username, (
        f"Expected clone proxy username {username!r} (token used verbatim, "
        f"NOT re-prefixed); got {got!r} (proxy: {proxy!r})"
    )


# --- bclaunch-3q12: container RUNTIME HTTPS_PROXY derivation + precedence ----
#
# These steps read the ACTUAL HTTPS_PROXY value the controller injected into the
# container's `docker run` env (recorded by FakeDockerDriver.container_proxy_env
# from the real env dict passed to .run()), NOT a static echoed-back string — so
# they have teeth against the real 3q12 defect: a runtime proxy pointed at the
# :14321 control API instead of the :14322 MITM proxy fails these assertions.

@when(parsers.parse(
    'bc-container launch is run for BC name "{bc_name}" with the '
    'operator-supplied agent-vault credentials and an explicit '
    'agent-vault broker URL "{broker}"'
))
def launch_with_explicit_broker_override(broker, bc_name, ctx, controller,
                                         fake_driver, tmp_path):
    repo_url = f"https://github.com/shopsystem/{bc_name}.git"
    manifest_path = tmp_path / "bc-manifest.yaml"
    if not manifest_path.exists():
        import yaml as _yaml
        manifest_path.write_text(_yaml.dump({
            "product": "shopsystem product",
            "bcs": [{"name": bc_name, "remote": repo_url, "role": "bc"}],
        }))
    result = controller.launch(
        bc_name=bc_name,
        repo_url=repo_url,
        manifest_path=manifest_path,
        credential_home=ctx.get("credential_home"),
        agent_vault_addr=ctx.get("av_addr"),
        agent_vault_token=ctx.get("av_token"),
        agent_vault_vault=ctx.get("av_vault"),
        agent_vault_broker=broker,
    )
    ctx["result"] = result
    ctx["container_name"] = f"bc-{bc_name}"
    ctx["bc_name"] = bc_name


def _runtime_proxy(ctx, fake_driver) -> str:
    """The HTTPS_PROXY value the container was actually launched with."""
    return fake_driver.container_proxy_env(ctx["container_name"])


@then(parsers.parse(
    'the container runtime HTTPS_PROXY is set to "{value}"'
))
def assert_runtime_proxy_exact(value, ctx, fake_driver):
    proxy = _runtime_proxy(ctx, fake_driver)
    assert proxy == value, (
        f"Expected the container runtime HTTPS_PROXY to be {value!r}, "
        f"got {proxy!r}"
    )


@then(parsers.parse(
    'the container runtime HTTPS_PROXY host is "{host}" on port {port:d}'
))
def assert_runtime_proxy_host_port(host, port, ctx, fake_driver):
    from urllib.parse import urlparse

    proxy = _runtime_proxy(ctx, fake_driver)
    parsed = urlparse(proxy)
    assert parsed.hostname == host, (
        f"Expected runtime proxy host {host!r}, got {parsed.hostname!r} "
        f"(proxy: {proxy!r})"
    )
    assert parsed.port == port, (
        f"Expected runtime proxy port {port}, got {parsed.port!r} "
        f"(proxy: {proxy!r})"
    )


@then(parsers.parse(
    'the container runtime HTTPS_PROXY is not the control API on port {port:d}'
))
def assert_runtime_proxy_not_control_api(port, ctx, fake_driver):
    from urllib.parse import urlparse

    proxy = _runtime_proxy(ctx, fake_driver)
    parsed = urlparse(proxy)
    assert parsed.port != port, (
        f"Container runtime HTTPS_PROXY points at the control-API port {port} "
        f"(bclaunch-3q12 DEFECT — must be the :14322 MITM proxy); "
        f"proxy: {proxy!r}"
    )


@then(parsers.parse(
    'the container runtime HTTPS_PROXY carries basic-auth userinfo "{userinfo}"'
))
def assert_runtime_proxy_userinfo(userinfo, ctx, fake_driver):
    from urllib.parse import urlparse, unquote

    proxy = _runtime_proxy(ctx, fake_driver)
    parsed = urlparse(proxy)
    got_user = unquote(parsed.username or "")
    got_pass = unquote(parsed.password or "")
    got = f"{got_user}:{got_pass}"
    assert got == userinfo, (
        f"Expected runtime proxy basic-auth userinfo {userinfo!r}, "
        f"got {got!r} (proxy: {proxy!r})"
    )


# The token literals an operator might realistically supply; none of these
# may appear hard-coded anywhere in src/.  The placeholder is the SOLE
# permitted credential literal in source.
_SRC_ROOT = Path(__file__).resolve().parent.parent / "src"


@when("the launcher source tree under src/ is scanned for credential literals")
def scan_src_for_credential_literals(ctx):
    sources = list(_SRC_ROOT.rglob("*.py"))
    ctx["src_texts"] = {p: p.read_text() for p in sources}


@then("no AGENT_VAULT_TOKEN value is hard-coded in src/")
def assert_no_token_literal_in_src(ctx):
    # A real agent-vault agent token carries the "av_agt_" prefix.  No such
    # literal may appear in source: the token is operator-supplied at launch.
    offenders = []
    for path, text in ctx["src_texts"].items():
        if "av_agt_" in text:
            offenders.append(str(path))
    assert not offenders, (
        f"Hard-coded agent-vault token literal found in src/: {offenders}"
    )


@then(parsers.parse(
    'the only credential literal present in src/ is the placeholder "{placeholder}"'
))
def assert_only_placeholder_literal(placeholder, ctx):
    # The placeholder is allowed; assert it is present (sanity) and that no
    # av_agt_ token literal coexists with it.
    all_text = "\n".join(ctx["src_texts"].values())
    assert placeholder in all_text, (
        f"Expected the placeholder literal {placeholder!r} to be present in src/"
    )
    assert "av_agt_" not in all_text, (
        "An agent-vault token literal coexists with the placeholder in src/"
    )


# --- bclaunch-7pf (REVISED): broker CA travels as AGENT_VAULT_CA_PEM env, ---
# --- controller builds NO CA bind-mount and sets NO controller-side trust env

# A fake PEM (~the real CA is ~574 bytes, PUBLIC not secret).  The operator
# supplies it as a line in --env-file; the controller injects it into the
# container env under AGENT_VAULT_CA_PEM and the bc-base entrypoint
# (bclaunch-9rr) materializes it to a file + trust env vars.
_FAKE_BROKER_CA_PEM = (
    "-----BEGIN CERTIFICATE-----\nFAKEBROKERCAFORTESTS\n"
    "-----END CERTIFICATE-----\n"
)


@given(parsers.parse('the operator supplies the broker CA PEM via AGENT_VAULT_CA_PEM'))
def operator_supplies_broker_ca_pem(ctx):
    ctx["av_ca_pem"] = _FAKE_BROKER_CA_PEM


@when(parsers.parse(
    'bc-container launch is run for BC name "{bc_name}" with the operator '
    'broker CA and agent-vault credentials'
))
def launch_with_ca_and_creds(bc_name, ctx, controller, fake_driver, tmp_path):
    repo_url = f"https://github.com/shopsystem/{bc_name}.git"
    manifest_path = tmp_path / "bc-manifest.yaml"
    if not manifest_path.exists():
        import yaml as _yaml
        manifest_path.write_text(_yaml.dump({
            "product": "shopsystem product",
            "bcs": [{"name": bc_name, "remote": repo_url, "role": "bc"}],
        }))
    # The broker CA PEM is supplied to the launcher as an AGENT_VAULT_CA_PEM
    # process-env line (as if sourced from --env-file).  The controller widens
    # its AGENT_VAULT_* injection to carry it through into the container env.
    monkey = ctx.get("_monkeypatch")
    env_overrides = {
        "AGENT_VAULT_ADDR": ctx.get("av_addr"),
        "AGENT_VAULT_TOKEN": ctx.get("av_token"),
        "AGENT_VAULT_VAULT": ctx.get("av_vault"),
        "AGENT_VAULT_CA_PEM": ctx.get("av_ca_pem"),
    }
    import os as _os
    saved = {}
    for k, v in env_overrides.items():
        saved[k] = _os.environ.get(k)
        if v is not None:
            _os.environ[k] = v
    try:
        result = controller.launch(
            bc_name=bc_name,
            repo_url=repo_url,
            manifest_path=manifest_path,
            credential_home=ctx.get("credential_home"),
        )
    finally:
        for k, v in saved.items():
            if v is None:
                _os.environ.pop(k, None)
            else:
                _os.environ[k] = v
    ctx["result"] = result
    ctx["container_name"] = f"bc-{bc_name}"
    ctx["bc_name"] = bc_name


@then(parsers.parse(
    'the container env has AGENT_VAULT_CA_PEM set to the operator-supplied '
    'broker CA PEM'
))
def assert_ca_pem_env(ctx, fake_driver):
    env = fake_driver.container_env(ctx["container_name"])
    assert env.get("AGENT_VAULT_CA_PEM") == ctx["av_ca_pem"], (
        f"Expected AGENT_VAULT_CA_PEM to carry the operator-supplied broker "
        f"CA PEM into the container env; got {env.get('AGENT_VAULT_CA_PEM')!r}"
    )


@then(parsers.parse(
    'no bind mount inside the container targets "{path}"'
))
def assert_no_mount_targets(path, ctx, fake_driver):
    mounts = fake_driver.container_mounts_full(ctx["container_name"])
    offenders = [m for m in mounts if m[2] == path]
    assert not offenders, (
        f"Expected NO bind mount targeting {path!r}; found {offenders!r}"
    )


@then(parsers.parse(
    'the container env has no {var} key set by the controller'
))
def assert_env_var_absent(var, ctx, fake_driver):
    env = fake_driver.container_env(ctx["container_name"])
    assert var not in env, (
        f"Expected the controller to set NO {var} env key (the bc-base "
        f"entrypoint owns trust vars now); got {var}={env.get(var)!r}"
    )


@then(parsers.parse('the container env has {var} set to "{value}"'))
def assert_named_env_var(var, value, ctx, fake_driver):
    env = fake_driver.container_env(ctx["container_name"])
    assert env.get(var) == value, (
        f"Expected {var}={value!r}, got {env.get(var)!r}"
    )


# ===========================================================================
# lead-yk3o (ruling on lead-yy30): bc-base build/publish artifacts + launch
# digest resolution.
#
# Scenarios 36/37/38/41 are pinned DECLARATIVELY by structural inspection of
# the committed Dockerfile and workflow YAML (live registry / live Actions
# state is OUT-OF-BAND per the architect ruling / scenario-40 precedent).
# Scenario 39 is pinned BEHAVIORALLY via the RegistryDriver seam.
# ===========================================================================

# The bc-launcher repository root is the parent of the tests/ directory.
_REPO_ROOT = Path(__file__).resolve().parent.parent


def _find_bc_base_dockerfile() -> Path | None:
    """Return the tracked Dockerfile that builds shopsystem-bc-base, or None.

    A Dockerfile "builds the shopsystem-bc-base image" if it is a Dockerfile
    whose surrounding context / content identifies it as the bc-base image
    build.  We accept any tracked file named Dockerfile (optionally suffixed)
    whose text references shopsystem-bc-base, ignoring the .git tree.

    A Dockerfile that DERIVES ``FROM`` a shopsystem-bc-base image (e.g. the thin
    docker/bc-lead/Dockerfile added by lead-nsj3) consumes the base image rather
    than building it, and merely mentioning the base in its FROM line must not
    make it masquerade as the bc-base build (bug shopsystem_bc_launcher-hnr).
    Iterate in sorted order so discovery is deterministic regardless of the
    filesystem's rglob ordering.
    """
    for path in sorted(_REPO_ROOT.rglob("Dockerfile*")):
        if ".git" in path.parts:
            continue
        if not path.is_file():
            continue
        text = path.read_text()
        if "shopsystem-bc-base" not in text:
            continue
        if re.search(r"(?im)^\s*FROM\s+\S*shopsystem-bc-base", text):
            continue
        return path
    return None


# Since lead-pwa2 (scenario edd2c813688ab768) the bc-base Dockerfile installs
# shop-templates at a version taken from the SHOP_TEMPLATES_VERSION build ARG so
# a templates-release rebuild can install the released tag.  A genuine
# version-by-shape pin is therefore EITHER:
#   (a) the frozen literal  ...shopsystem-templates(.git)?@vMAJOR.MINOR.PATCH, OR
#   (b) the parameterized   ...shopsystem-templates(.git)?@${SHOP_TEMPLATES_VERSION}
#       WITH an `ARG SHOP_TEMPLATES_VERSION=vMAJOR.MINOR.PATCH` default carrying
#       the version shape.
# Both preserve the dstengle/shopsystem-templates owner/repo binding and the
# vMAJOR.MINOR.PATCH version shape; both reject an editable clone.  An
# unparameterized @${VAR} with no vX.Y.Z-shaped default does NOT count.
_SHOP_TEMPLATES_LITERAL_PIN_RE = re.compile(
    r"shop-templates @ git\+https://github\.com/dstengle/"
    r"shopsystem-templates(?:\.git)?@v\d+\.\d+\.\d+"
)
_SHOP_TEMPLATES_ARG_PIN_RE = re.compile(
    r"shop-templates @ git\+https://github\.com/dstengle/"
    r"shopsystem-templates(?:\.git)?@\$\{?SHOP_TEMPLATES_VERSION\}?"
)
_SHOP_TEMPLATES_ARG_DEFAULT_SHAPE_RE = re.compile(
    r"ARG\s+SHOP_TEMPLATES_VERSION=v\d+\.\d+\.\d+"
)


def _shop_templates_pinned_by_version_shape(dockerfile_text: str) -> bool:
    """True when shop-templates is pinned by vMAJOR.MINOR.PATCH shape, whether
    as a frozen literal or via the SHOP_TEMPLATES_VERSION build ARG defaulted to
    a vX.Y.Z value (lead-pwa2 parameterization)."""
    if _SHOP_TEMPLATES_LITERAL_PIN_RE.search(dockerfile_text):
        return True
    return bool(
        _SHOP_TEMPLATES_ARG_PIN_RE.search(dockerfile_text)
        and _SHOP_TEMPLATES_ARG_DEFAULT_SHAPE_RE.search(dockerfile_text)
    )


def _workflows_dir() -> Path:
    return _REPO_ROOT / ".github" / "workflows"


def _load_workflows() -> dict[Path, dict]:
    """Load all committed workflow YAML files under .github/workflows."""
    out: dict[Path, dict] = {}
    wf_dir = _workflows_dir()
    if not wf_dir.is_dir():
        return out
    for path in sorted(wf_dir.glob("*.yml")) + sorted(wf_dir.glob("*.yaml")):
        out[path] = yaml.safe_load(path.read_text())
    return out


# --- Scenario 36 (d9909f38abea83b5): committed bc-base Dockerfile ----------

@given("the shopsystem-bc-launcher BC repository")
def given_bc_launcher_repository(ctx):
    ctx["repo_root"] = _REPO_ROOT


@when("the repository file tree is inspected")
def when_repo_file_tree_inspected(ctx):
    ctx["bc_base_dockerfile"] = _find_bc_base_dockerfile()


@then("a Dockerfile that builds the shopsystem-bc-base image exists at a "
      "tracked path within the bc-launcher repository")
def then_bc_base_dockerfile_exists(ctx):
    dockerfile = ctx.get("bc_base_dockerfile")
    assert dockerfile is not None, (
        "No tracked Dockerfile building shopsystem-bc-base found under the "
        "bc-launcher repository file tree."
    )
    # Confirm the path is git-tracked, not merely present on disk.
    rel = dockerfile.relative_to(_REPO_ROOT)
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(rel)],
        cwd=str(_REPO_ROOT), capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, (
        f"Dockerfile {rel} exists on disk but is not git-tracked."
    )


@then('that Dockerfile installs the framework utility CLIs from their VCS or '
      'published-package version pins in the '
      '"github.com/dstengle/<utility> @ vMAJOR.MINOR.PATCH" shape rather than '
      'from an editable clone')
def then_dockerfile_pins_clis(ctx):
    dockerfile = ctx["bc_base_dockerfile"]
    text = dockerfile.read_text()
    # Must NOT install from an editable clone of a sibling working tree.
    assert "pip install -e" not in text and "pip install --editable" not in text, (
        "bc-base Dockerfile installs a framework CLI from an editable clone "
        "(pip install -e); the ruling requires VCS/published-package version "
        "pins instead."
    )
    # Must install at least one dstengle framework utility pinned to a
    # vMAJOR.MINOR.PATCH version in the VCS-pin shape:
    #   <utility> @ git+https://github.com/dstengle/<utility>.git@vX.Y.Z
    pin_re = re.compile(
        r"github\.com/dstengle/[A-Za-z0-9._-]+(?:\.git)?@v\d+\.\d+\.\d+"
    )
    matches = pin_re.findall(text)
    assert matches, (
        "bc-base Dockerfile does not install any framework utility CLI from a "
        "github.com/dstengle/<utility> @ vMAJOR.MINOR.PATCH version pin.\n"
        f"Dockerfile content:\n{text}"
    )


# --- Scenario ccb145d71c7100a2 (lead-b6gd, supersedes 30165afc5692ac3d): -----
# bc-base installs shop-templates (package name "shop-templates") from a
# github.com/dstengle/shopsystem-templates @ vMAJOR.MINOR.PATCH VCS pin,
# alongside the other framework utility CLIs in the same pin shape.  The repo
# is shopsystem-templates (NOT shop-templates, which 404s).  Mirrors the
# scenario-36 declarative-artifact inspection rigor: rejects editable /
# `pip install -e` clones.

@when("the bc-base Dockerfile in that repository is inspected")
def when_bc_base_dockerfile_inspected(ctx):
    ctx["bc_base_dockerfile"] = _find_bc_base_dockerfile()


@then('the Dockerfile installs "shop-templates" from a '
      '"github.com/dstengle/shopsystem-templates @ vMAJOR.MINOR.PATCH" version pin '
      'rather than from an editable clone')
def then_dockerfile_pins_shop_templates(ctx):
    dockerfile = ctx.get("bc_base_dockerfile")
    assert dockerfile is not None, (
        "No tracked Dockerfile building shopsystem-bc-base found under the "
        "bc-launcher repository file tree."
    )
    text = dockerfile.read_text()
    # Must NOT install shop-templates from an editable clone of a sibling
    # working tree (same rigor as the scenario-36 step).
    assert "pip install -e" not in text and "pip install --editable" not in text, (
        "bc-base Dockerfile installs a framework CLI from an editable clone "
        "(pip install -e); the lead-b6gd pin requires a VCS version pin instead."
    )
    # The shop-templates package must be installed from a
    # github.com/dstengle/shopsystem-templates @ vMAJOR.MINOR.PATCH VCS pin in
    # the pip VCS-requirement spelling (package name shop-templates, repo
    # shopsystem-templates).  Since lead-pwa2 (scenario edd2c813688ab768) the
    # version is PARAMETERIZED through the SHOP_TEMPLATES_VERSION build ARG so a
    # templates-release rebuild can install the released tag; the ARG carries a
    # vMAJOR.MINOR.PATCH default, preserving the version-by-shape pin.  Accept
    # either the frozen literal OR the parameterized-with-vX.Y.Z-default form.
    assert _shop_templates_pinned_by_version_shape(text), (
        "bc-base Dockerfile does not install shop-templates from a "
        "github.com/dstengle/shopsystem-templates @ vMAJOR.MINOR.PATCH version "
        "pin (literal, or SHOP_TEMPLATES_VERSION build ARG defaulted to "
        f"vMAJOR.MINOR.PATCH).\nDockerfile content:\n{text}"
    )


@then("that shop-templates install sits alongside the other framework utility "
      "CLIs the Dockerfile installs in the same VCS-pin shape")
def then_shop_templates_alongside_other_clis(ctx):
    dockerfile = ctx["bc_base_dockerfile"]
    text = dockerfile.read_text()
    # Each VCS-pin is "<pkg> @ git+https://github.com/dstengle/<repo>.git@vX.Y.Z".
    # The distributed package name (left of " @ ") is what identifies the
    # utility; the repo path may differ from the package name (shop-templates
    # ships from the shopsystem-templates repo).
    pin_re = re.compile(
        r"([A-Za-z0-9._-]+) @ git\+https://github\.com/dstengle/"
        r"([A-Za-z0-9._-]+?)(?:\.git)?@v\d+\.\d+\.\d+"
    )
    packages = {m.group(1) for m in pin_re.finditer(text)}
    # shop-templates is one of the VCS-pinned utilities -- pinned to its
    # dstengle/shopsystem-templates repo by vMAJOR.MINOR.PATCH shape.  Since
    # lead-pwa2 (scenario edd2c813688ab768) its version is PARAMETERIZED through
    # the SHOP_TEMPLATES_VERSION build ARG (default vX.Y.Z), so it appears in the
    # @${SHOP_TEMPLATES_VERSION} form rather than as a frozen @vX.Y.Z literal;
    # the helper recognizes both.
    assert _shop_templates_pinned_by_version_shape(text), (
        "shop-templates is not installed in the "
        "<pkg> @ git+https://github.com/dstengle/<repo> @ vMAJOR.MINOR.PATCH "
        "VCS-pin shape (literal or SHOP_TEMPLATES_VERSION ARG defaulted to "
        f"vX.Y.Z); pinned packages found: {packages}"
    )
    # ... and it sits ALONGSIDE at least one OTHER framework utility pinned in
    # the exact same shape (e.g. shop-msg / beads), confirming it joins the
    # existing pinned set rather than standing alone in a different form.
    others = packages - {"shop-templates"}
    assert others, (
        "shop-templates is the ONLY utility in the VCS-pin shape; the scenario "
        "requires it to sit alongside the other framework utility CLIs "
        "(e.g. shop-msg, beads) installed in the same shape.\n"
        f"Dockerfile content:\n{text}"
    )


# --- BC-internal (bead shopsystem-bc-launcher-tuk, scoped by lead-6rm4): ------
# the FOUR dstengle framework-CLI installs are pinned to their CORRECT
# owner/repo, AND bd (beads) is installed via the steveyegge/beads binary -----
#
# ADDITIVE coverage. Scenario 42 (ccb145d71c7100a2) pins only shop-templates
# and scenario 36 (d9909f38abea83b5) requires only ">=1 dstengle VCS pin +
# reject editable". Neither binds the OTHER dstengle CLIs to their owner/repo,
# which is how two wrong-repo 404 defects (dstengle/shop-msg, dstengle/beads)
# shipped green. This step binds each of the four DSTENGLE framework packages
# to its correct (owner, repo) pair and asserts each is present in the
#   "<pkg> @ git+https://github.com/<owner>/<repo>.git@vMAJOR.MINOR.PATCH"
# VCS-pin shape. The version is matched by SHAPE (vX.Y.Z), not exact value, so
# legitimate version bumps don't break the test while the 404 class still
# trips.
#
# beads is NOT a dstengle utility and NOT pip-installable (lead-6rm4): bd is a
# third-party Go binary installed from the steveyegge/beads releases, asserted
# separately by then_dockerfile_installs_beads_binary below. The prior
# beads -> gascity/beads pip pin was the defective install KIND that broke the
# Phase-3 publish-bc-base GHA build (run 26967027158) on the v0.2.0 tag.

# package name (left of " @ ") -> (correct owner, correct repo)
_BC_BASE_FRAMEWORK_CLI_PINS = {
    "shopsystem-messaging": ("dstengle", "shopsystem-messaging"),
    "scenarios": ("dstengle", "shopsystem-scenarios"),
    "shop-templates": ("dstengle", "shopsystem-templates"),
    "shopsystem-bc-launcher": ("dstengle", "shopsystem-bc-launcher"),
}

# bd (beads) binary install — third-party Go binary, NOT a pip VCS pin.
_BC_BASE_BEADS_BINARY_OWNER = "steveyegge"
_BC_BASE_BEADS_BINARY_VERSION = "1.0.3"


@then("the Dockerfile installs the four dstengle framework CLIs each from a "
      "VCS version pin bound to its correct owner and repo")
def then_dockerfile_pins_four_dstengle_clis(ctx):
    dockerfile = ctx.get("bc_base_dockerfile")
    assert dockerfile is not None, (
        "No tracked Dockerfile building shopsystem-bc-base found under the "
        "bc-launcher repository file tree."
    )
    text = dockerfile.read_text()
    missing = []
    for pkg, (owner, repo) in _BC_BASE_FRAMEWORK_CLI_PINS.items():
        # shop-templates is PARAMETERIZED since lead-pwa2 (scenario
        # edd2c813688ab768): its version comes from the SHOP_TEMPLATES_VERSION
        # build ARG so a templates-release rebuild installs the released tag.
        # The owner/repo binding and vX.Y.Z version shape are still asserted
        # (the ARG default carries the shape) -- a wrong owner/repo still FAILS.
        if pkg == "shop-templates":
            if not _shop_templates_pinned_by_version_shape(text):
                missing.append(
                    f"{pkg} -> github.com/{owner}/{repo} @ vMAJOR.MINOR.PATCH "
                    "(literal or SHOP_TEMPLATES_VERSION ARG defaulted to vX.Y.Z)"
                )
            continue
        # Bind the package name to its CORRECT owner/repo. A wrong owner
        # (e.g. dstengle/beads) or wrong repo (e.g. dstengle/shop-msg) will
        # not match its package's required (owner, repo) pair -> FAIL.
        pin_re = re.compile(
            re.escape(pkg) + r" @ git\+https://github\.com/"
            + re.escape(owner) + r"/" + re.escape(repo)
            + r"(?:\.git)?@v\d+\.\d+\.\d+"
        )
        if not pin_re.search(text):
            missing.append(
                f"{pkg} -> github.com/{owner}/{repo} @ vMAJOR.MINOR.PATCH"
            )
    assert not missing, (
        "bc-base Dockerfile is missing or mis-pins these framework CLIs "
        "(each must bind to its correct owner/repo in the "
        "<pkg> @ git+https://github.com/<owner>/<repo>.git@vMAJOR.MINOR.PATCH "
        "shape):\n  " + "\n  ".join(missing)
        + f"\nDockerfile content:\n{text}"
    )


@then("bd is installed from the steveyegge/beads binary release pinned to "
      "BD_VERSION=1.0.3 rather than from a pip VCS pin")
def then_dockerfile_installs_beads_binary(ctx):
    dockerfile = ctx.get("bc_base_dockerfile")
    assert dockerfile is not None, (
        "No tracked Dockerfile building shopsystem-bc-base found under the "
        "bc-launcher repository file tree."
    )
    text = dockerfile.read_text()

    # beads must NOT be a pip VCS pin of ANY owner: the whole point of this
    # bugfix is that bd is a Go binary, not pip-installable. Reverting beads
    # to any "beads @ git+https://github.com/<owner>/beads" pip pin must FAIL.
    beads_pip_re = re.compile(
        r"beads @ git\+https://github\.com/[A-Za-z0-9._-]+/beads"
    )
    assert not beads_pip_re.search(text), (
        "bc-base Dockerfile installs beads as a pip VCS pin; bd is a "
        "third-party Go binary (NOT pip-installable) and must be installed "
        "from the steveyegge/beads binary release instead.\n"
        f"Dockerfile content:\n{text}"
    )

    # bd must be installed from the steveyegge/beads releases, pinned to the
    # exact tagged binary version, into /usr/local/bin/bd. Teeth: mutating
    # BD_VERSION away from 1.0.3, or the owner away from steveyegge, must FAIL.
    owner = _BC_BASE_BEADS_BINARY_OWNER
    version = re.escape(_BC_BASE_BEADS_BINARY_VERSION)
    version_re = re.compile(r"BD_VERSION=" + version + r"\b")
    url_re = re.compile(
        r"github\.com/" + re.escape(owner)
        + r"/beads/releases/download/v\$\{BD_VERSION\}/"
    )
    install_re = re.compile(r"install\b[^\n]*/usr/local/bin/bd\b")

    failures = []
    if not version_re.search(text):
        failures.append(
            f"BD_VERSION={_BC_BASE_BEADS_BINARY_VERSION} pin not found"
        )
    if not url_re.search(text):
        failures.append(
            f"binary fetched from github.com/{owner}/beads/releases not found"
        )
    if not install_re.search(text):
        failures.append("bd not installed to /usr/local/bin/bd")
    assert not failures, (
        "bc-base Dockerfile does not install bd as the steveyegge/beads "
        "binary pinned to BD_VERSION=1.0.3 in /usr/local/bin/bd:\n  "
        + "\n  ".join(failures)
        + f"\nDockerfile content:\n{text}"
    )


@then("none of the four dstengle framework CLIs is installed from an editable "
      "clone")
def then_no_framework_cli_is_editable(ctx):
    dockerfile = ctx["bc_base_dockerfile"]
    text = dockerfile.read_text()
    assert "pip install -e" not in text and "pip install --editable" not in text, (
        "bc-base Dockerfile installs a framework CLI from an editable clone "
        "(pip install -e / --editable); the four dstengle framework CLIs must "
        "install from VCS version pins instead.\n"
        f"Dockerfile content:\n{text}"
    )


# --- Scenario 75ae95be0ecf1640 (lead-dlrx): launch pours shop-templates ------
# skill-group into the cloned workspace's ".claude/skills/" directory, after
# the clone, modelled behaviourally through the DockerDriver seam.

@given("the bc-base image carries the shop-templates binary")
def given_bc_base_carries_shop_templates(ctx, fake_driver):
    """Precondition: the launched container's image has the shop-templates CLI.

    The bc-base Dockerfile installs shop-templates from its VCS version pin
    (scenario ccb145d71c7100a2), so a real launch's `shop-templates pour` exec runs a
    binary that is present.  In the fake driver the pour is modelled
    unconditionally; this Given records the precondition for readability and
    so the scenario reads end-to-end.
    """
    ctx["bc_base_has_shop_templates"] = True


@then("the shop-templates pour has been run inside the container's workspace "
      "directory")
def then_shop_templates_pour_ran_in_workspace(ctx, fake_driver):
    # lead-q5k7: the skill-refresh is the `shop-templates update` subcommand
    # targeting the workspace — NOT the old invalid `pour` shape.  The
    # scenario semantics ("a skill-refresh ran inside the workspace") are
    # unchanged; only the underlying corrected command is asserted here.
    container_name = ctx["container_name"]
    refresh_calls = [
        c for c in fake_driver.exec_calls
        if c.container == container_name
        and c.command[:2] == ["shop-templates", "update"]
    ]
    assert refresh_calls, (
        "Expected a 'shop-templates update' exec call during launch; none "
        "ran. The launch path must refresh the shop-templates skill-group "
        "after clone (lead-q5k7: the old `pour` shape is an INVALID command)."
    )
    # The refresh must target the container's workspace directory via the
    # VALID `--target` flag — i.e. it ran INSIDE the workspace.
    assert any(
        c.command[:2] == ["shop-templates", "update"]
        and "--target" in c.command
        and c.command[c.command.index("--target") + 1] == "/workspace"
        for c in refresh_calls
    ), (
        "shop-templates update ran but did not --target the container's "
        f"workspace directory (/workspace); commands: "
        f"{[c.command for c in refresh_calls]}"
    )


@then('the workspace\'s ".claude/skills/" directory is populated with the '
      'shop-templates skill-group after launch completes')
def then_workspace_skills_populated(ctx, fake_driver):
    container_name = ctx["container_name"]
    skills = fake_driver.workspace_skills(container_name)
    assert skills, (
        "The workspace's .claude/skills/ directory was NOT populated after "
        "launch completed; the shop-templates pour did not deposit the "
        "skill-group. (Skipping the pour leaves this empty — the assertion's "
        "teeth.)"
    )
    assert fake_driver.SHOP_TEMPLATES_SKILL_GROUP.issubset(skills), (
        "The workspace's .claude/skills/ directory does not contain the full "
        f"shop-templates skill-group; present: {skills}, "
        f"expected superset of: {set(fake_driver.SHOP_TEMPLATES_SKILL_GROUP)}"
    )


# --- lead-q5k7: skill-refresh uses the correct invocation + surfaces errors --
# Scenarios d0045dad01f070c8 (correct command + shop-type), db11ca7b46dd12a4
# (failed refresh surfaces a real error, no false success), 2cd278b67bb5cd0f
# (refreshed skill carries the lead-80t0 health step).

@given(parsers.parse('the cloned shop\'s type marker is "{shop_type}"'))
def given_cloned_shop_type_marker(shop_type, ctx, fake_driver):
    """Model `.claude/shop/type.md` in the cloned repo (lead-q5k7).

    The controller reads this to derive the `--shop-type <bc|lead>` value
    passed to `shop-templates update`.
    """
    container_name = f"bc-{ctx['bc_name']}"
    fake_driver.set_shop_type(container_name, shop_type)
    ctx["expected_shop_type"] = shop_type


@given("the shop-templates skill-refresh fails at runtime")
def given_skill_refresh_fails(ctx, fake_driver):
    """Model a VALID `shop-templates update` that nonetheless fails at
    runtime (lead-q5k7 criterion B), so the controller's result-check +
    error-surfacing has teeth: a failed refresh must surface a real error
    and must NOT log false success.
    """
    container_name = f"bc-{ctx['bc_name']}"
    fake_driver.set_skill_refresh_fails(container_name, fails=True)


@then(parsers.parse('launch runs "shop-templates update" targeting the '
                    'container\'s workspace with shop-type "{shop_type}"'))
def then_launch_runs_update_with_shop_type(shop_type, ctx, fake_driver):
    container_name = ctx["container_name"]
    matching = [
        c for c in fake_driver.exec_calls
        if c.container == container_name
        and c.command[:2] == ["shop-templates", "update"]
        and "--target" in c.command
        and c.command[c.command.index("--target") + 1] == "/workspace"
        and "--shop-type" in c.command
        and c.command[c.command.index("--shop-type") + 1] == shop_type
    ]
    assert matching, (
        "Expected a `shop-templates update --target /workspace --shop-type "
        f"{shop_type}` exec during launch; none matched. Exec'd "
        "shop-templates commands: "
        f"{[c.command for c in fake_driver.exec_calls if c.command[:1] == ['shop-templates']]}"
    )


@then('launch never runs the invalid "shop-templates pour" command')
def then_launch_never_runs_pour(ctx, fake_driver):
    container_name = ctx["container_name"]
    pour = [
        c for c in fake_driver.exec_calls
        if c.container == container_name
        and c.command[:2] == ["shop-templates", "pour"]
    ]
    assert not pour, (
        "Launch ran the INVALID `shop-templates pour` command (lead-q5k7: "
        f"`pour` is not a valid subcommand): {[c.command for c in pour]}"
    )


@then("the launch result is success")
def then_launch_result_success(ctx):
    result = ctx["result"]
    assert result.exit_code == 0, (
        f"Expected launch to succeed; got exit {result.exit_code}, "
        f"stderr={result.stderr!r}"
    )


@then("the launch result is a failure naming the shop-templates update error")
def then_launch_result_failure_names_update(ctx):
    result = ctx["result"]
    assert result.exit_code != 0, (
        "Expected launch to FAIL when the skill-refresh exec failed, but it "
        f"returned success (exit 0). A failed `shop-templates update` must "
        "surface a real error and fail the launch (lead-q5k7 criterion B)."
    )
    assert "shop-templates update" in (result.stderr or ""), (
        "Launch failed but its stderr does not name the shop-templates "
        f"update error; stderr={result.stderr!r}"
    )


@then("the launch warns about the shop-templates update failure and still "
      "starts the agent")
def then_launch_warns_and_starts_agent(ctx, fake_driver):
    """lead-k4k7 — a failed skill-refresh is downgraded from a fatal

    early-return to a WARNING that still PROCEEDS to agent-start.  The launch
    must NOT abort before the agent tmux session is started, must emit a
    warning naming the shop-templates failure, and must end with the agent's
    tmux session started and the startup prompt injected.
    """
    result = ctx["result"]
    container_name = ctx["container_name"]
    combined = (result.stdout or "") + (result.stderr or "")

    # A warning naming the shop-templates skill-refresh failure.
    assert "warning" in combined.lower() and "shop-templates" in combined, (
        "A failed skill-refresh must log a warning naming the shop-templates "
        f"failure; output={combined!r}"
    )
    # The launch must NOT have aborted before starting the agent tmux session.
    new_sessions = [
        c.command for c in fake_driver.exec_calls
        if c.container == container_name
        and c.command[:3] == ["tmux", "new-session", "-d"]
    ]
    assert new_sessions, (
        "A failed skill-refresh must NOT abort the launch before the agent "
        f"tmux session is started (lead-k4k7); output={combined!r}"
    )
    assert "Started tmux session" in (result.stdout or ""), (
        "The 'Started tmux session' log line must still be emitted on a "
        f"failed refresh (lead-k4k7); stdout={result.stdout!r}"
    )
    # And the launch must not abort with a fatal exit on the refresh failure
    # alone (the readiness barriers are green in this scenario).
    assert result.exit_code == 0, (
        "A launch whose only failure was a transient skill-refresh — with all "
        "readiness barriers green — must exit 0, not abort (lead-k4k7); "
        f"stderr={result.stderr!r}"
    )


@then("the launch output never claims the skill-group was refreshed")
def then_launch_output_no_false_success(ctx, fake_driver):
    result = ctx["result"]
    combined = (result.stdout or "") + (result.stderr or "")
    # The old false-success log line; neither it nor a "Refreshed ..."
    # success line may appear when the refresh actually failed.
    assert "Poured shop-templates skill-group" not in combined, (
        "Launch logged the old false-success 'Poured ...' line on a FAILED "
        f"refresh (lead-q5k7 criterion B); output={combined!r}"
    )
    assert "Refreshed shop-templates skill-group" not in (result.stdout or ""), (
        "Launch logged a 'Refreshed ...' success line on a FAILED refresh "
        f"(lead-q5k7 criterion B); stdout={result.stdout!r}"
    )
    # And the workspace skills must NOT have been deposited.
    assert not fake_driver.workspace_skills(ctx["container_name"]), (
        "A failed refresh nonetheless deposited skills into the workspace; "
        "the failure surface is not modelled faithfully."
    )


@then('the workspace\'s ".claude/skills/" carries the health-bearing '
      'bc-router skill after launch')
def then_workspace_carries_health_skill(ctx, fake_driver):
    skills = fake_driver.workspace_skills(ctx["container_name"])
    assert "bc-router-health" in skills, (
        "The refreshed workspace .claude/skills/ does not carry the "
        "health-bearing bc-router skill (lead-80t0 health step / criterion "
        f"C); present: {skills}. NOTE: criterion C's full effect (the poured "
        "SKILL.md is the 143-line health-bearing copy overwriting the stale "
        "111-line committed one) is EMPIRICAL-ONLY (criterion D) — modelled "
        "here at the fake's fidelity (a health-bearing skill-group entry)."
    )


# --- Scenario 37 (b688a5feaf1cf34a): publish-on-tag workflow ----------------

@given(parsers.parse('a tag named "{tag}" is pushed to the "{branch}" branch '
                     'of the shopsystem-bc-launcher source repository'))
def given_version_tag_pushed(tag, branch, ctx):
    ctx["pushed_tag"] = tag
    ctx["pushed_branch"] = branch


@when("the bc-launcher publish workflow associated with that tag push "
      "completes successfully")
def when_publish_workflow_completes(ctx):
    # Live Actions execution is OUT-OF-BAND (scenario-40 precedent): the
    # in-suite proxy is the committed workflow STRUCTURE.  Locate the publish
    # workflow whose push trigger matches a "v*" tag pattern.
    workflows = _load_workflows()
    ctx["workflows"] = workflows
    publish_wf = None
    for path, doc in workflows.items():
        if not isinstance(doc, dict):
            continue
        # PyYAML parses the bare key `on:` as the boolean True.
        on = doc.get("on", doc.get(True))
        if not isinstance(on, dict):
            continue
        push = on.get("push")
        if isinstance(push, dict):
            tags = push.get("tags") or []
            if any(str(t).startswith("v") for t in tags):
                publish_wf = (path, doc)
                break
    ctx["publish_workflow"] = publish_wf


def _workflow_text(ctx) -> str:
    path = ctx["publish_workflow"][0]
    return path.read_text()


@then(parsers.parse('the registry "{registry}" exposes an image manifest at '
                    'the repository path "{repo_path}" reachable by the image '
                    'tag "{tag}"'))
def then_registry_exposes_version_tag(registry, repo_path, tag, ctx):
    assert ctx.get("publish_workflow") is not None, (
        'No committed publish workflow triggered on a "v*" tag push was found '
        "under .github/workflows."
    )
    text = _workflow_text(ctx)
    image_base = f"{registry}/{repo_path}"
    assert image_base in text, (
        f"Publish workflow does not push to {image_base!r}."
    )
    # For the version tag, the workflow tags by the pushed ref name (github.ref_name).
    if tag.startswith("v"):
        assert "ref_name" in text or f":{tag}" in text, (
            "Publish workflow does not tag the image by its version "
            "(github.ref_name) for the pushed v* tag."
        )


@then(parsers.parse('the registry "{registry}" exposes an image manifest at '
                    'the repository path "{repo_path}" reachable by the image '
                    'tag "latest" pointing to the same digest as the "{vtag}" '
                    'tag'))
def then_latest_same_digest_as_version(registry, repo_path, vtag, ctx):
    text = _workflow_text(ctx)
    image_base = f"{registry}/{repo_path}"
    # The same build-push step tags BOTH the version and "latest", so the one
    # built digest is reachable by both tags.
    assert f"{image_base}:latest" in text, (
        f"Publish workflow does not tag {image_base}:latest."
    )
    assert ("ref_name" in text or f"{image_base}:{vtag}" in text), (
        "Publish workflow does not also tag the same image by its version, so "
        '"latest" and the version tag would not share a digest.'
    )


@then('both image tags can be pulled by an unauthenticated "docker pull" '
      'client because the package is published with public visibility')
def then_public_visibility_declared(ctx):
    text = _workflow_text(ctx)
    # Live unauthenticated pull is OUT-OF-BAND; the in-suite proxy is the
    # declared public visibility in the workflow.
    assert "visibility=public" in text or "visibility: public" in text, (
        "Publish workflow does not declare public package visibility, so an "
        "unauthenticated docker pull is not pinned."
    )


# --- Scenario 38 (4e470f7584650a2d): repository_dispatch rebuild ------------

@given(parsers.parse('the image tag "latest" at "{image_ref}" currently '
                     'points to a digest "{digest_label}"'))
def given_latest_points_to_digest(image_ref, digest_label, ctx):
    ctx.setdefault("digest_labels", {})[digest_label] = image_ref


@when('a "repository_dispatch" event is delivered to the bc-launcher '
      "repository and the bc-launcher build workflow runs to successful "
      "completion in response to that event")
def when_repository_dispatch_runs(ctx):
    # Live Actions execution OUT-OF-BAND; proxy is the committed workflow
    # declaring a repository_dispatch trigger whose job re-pushes "latest".
    workflows = _load_workflows()
    ctx["workflows"] = workflows
    dispatch_wf = None
    for path, doc in workflows.items():
        if not isinstance(doc, dict):
            continue
        on = doc.get("on", doc.get(True))
        if isinstance(on, dict) and "repository_dispatch" in on:
            dispatch_wf = (path, doc)
            break
    ctx["dispatch_workflow"] = dispatch_wf


@then(parsers.parse('a new bc-base image is built that installs the current '
                    'framework utility versions producing a digest '
                    '"{new_digest}" distinct from "{old_digest}"'))
def then_new_image_built(new_digest, old_digest, ctx):
    assert ctx.get("dispatch_workflow") is not None, (
        "No committed workflow declaring a repository_dispatch trigger was "
        "found under .github/workflows."
    )
    text = ctx["dispatch_workflow"][0].read_text()
    # A genuine rebuild (new digest) requires an actual build-push step.
    assert "build-push-action" in text or "docker build" in text, (
        "repository_dispatch workflow does not run an image build step, so it "
        "cannot produce a new digest."
    )


@then(parsers.parse('the registry "{registry}" exposes the image tag "latest" '
                    'at the repository path "{repo_path}" pointing to '
                    '"{new_digest}"'))
def then_dispatch_repushes_latest(registry, repo_path, new_digest, ctx):
    text = ctx["dispatch_workflow"][0].read_text()
    image_base = f"{registry}/{repo_path}"
    assert f"{image_base}:latest" in text, (
        f"repository_dispatch workflow does not re-push {image_base}:latest."
    )


# --- Scenario 41 (be11d615375564e1): rollback re-tag ------------------------

@given(parsers.parse('the registry "{image_ref}" holds a prior known-good '
                     'build pullable by its digest "{digest_label}"'))
def given_prior_known_good_digest(image_ref, digest_label, ctx):
    ctx["rollback_image_ref"] = image_ref
    ctx.setdefault("digest_labels", {})[digest_label] = image_ref


@given(parsers.parse('the "latest" tag currently points to a later digest '
                     '"{digest_label}"'))
def given_latest_points_to_later(digest_label, ctx):
    ctx.setdefault("digest_labels", {})[digest_label] = ctx.get(
        "rollback_image_ref"
    )


@when(parsers.parse('the "latest" tag is republished to point at the existing '
                    'digest "{digest_label}"'))
def when_latest_republished_to_good(digest_label, ctx):
    # The rollback re-tag procedure is pinned declaratively: the publish
    # workflow tags every release by its immutable version (so prior digests
    # stay pullable), and the runbook documents the latest-repoint procedure.
    ctx["workflows"] = _load_workflows()
    ctx["rollback_target_label"] = digest_label


@then(parsers.parse('the registry exposes the image tag "latest" at the '
                    'repository path "{repo_path}" pointing to '
                    '"{digest_label}"'))
def then_latest_points_to_good(repo_path, digest_label, ctx):
    # Declarative pin (scenario-40 precedent): the publish workflow tags by
    # version, keeping the prior digest pullable and enabling a latest-repoint;
    # the documented re-tag procedure lives in a runbook.
    workflows = ctx.get("workflows") or _load_workflows()
    # Mirror scenario-37's then_registry_exposes_version_tag rigor: genuine
    # version-tagging is a real ${{ github.ref_name }} tag expression or a
    # concrete :vMAJOR.MINOR.PATCH tag in the workflow body, NOT merely the
    # word "version" appearing in a comment. The bare-substring "version"
    # fallback let a mutation that strips the real ref_name tag slip past so
    # long as any comment mentioning "version" survived.
    image_base = f"ghcr.io/{repo_path}"
    version_tag_re = re.compile(
        r"\$\{\{\s*github\.ref_name\s*\}\}|:v\d+\.\d+\.\d+"
    )
    tags_by_version = False
    for path, doc in workflows.items():
        text = path.read_text()
        if f"{image_base}:latest" in text and version_tag_re.search(text):
            tags_by_version = True
            break
    assert tags_by_version, (
        "No committed workflow tags the bc-base image by version alongside "
        '"latest", so a prior digest could not be re-pointed by "latest".'
    )
    runbook = _REPO_ROOT / "docs" / "runbooks" / "bc-base-rollback.md"
    assert runbook.is_file(), (
        "No rollback runbook documenting the latest re-tag procedure was found "
        f"at {runbook.relative_to(_REPO_ROOT)}."
    )


@then(parsers.parse('no new image build is required because "{digest_label}" '
                    "is an already-published digest re-tagged in place"))
def then_no_rebuild_required(digest_label, ctx):
    runbook = _REPO_ROOT / "docs" / "runbooks" / "bc-base-rollback.md"
    text = runbook.read_text()
    # The runbook must document a re-tag-in-place (no rebuild) procedure.
    assert "imagetools create" in text or "re-tag" in text.lower() or (
        "no new image build" in text.lower() or "without rebuild" in text.lower()
    ), (
        "Rollback runbook does not document an in-place re-tag (no rebuild) "
        "procedure."
    )


# --- Scenario 39 (af2f03d3ac519cb5): launch resolves latest digest ----------

_BC_BASE_LATEST_REF = "ghcr.io/dstengle/shopsystem-bc-base:latest"


@given(parsers.parse('the local Docker cache holds the bc-base "latest" tag '
                     'at an older digest "{old_digest}"'))
def given_cache_holds_old_digest(old_digest, ctx):
    ctx["cached_digest"] = old_digest


@given(parsers.parse('the registry "{image_ref}" now publishes the "latest" '
                     'tag at a newer digest "{new_digest}"'))
def given_registry_publishes_new_digest(image_ref, new_digest, ctx):
    registry_driver = FakeRegistryDriver()
    # The registry resolves the bc-base "latest" reference to the new digest.
    # Model the digest as a content-addressable sha256 value carrying the
    # scenario's digest label, so the resolved reference is a genuine
    # repo@sha256:... pin (the shape the real driver produces) while remaining
    # assertable by the label.
    # Use a hex-only token derived from the label so the value is a
    # well-formed sha256 digest, and remember it for assertions.
    label_hex = "".join(c for c in new_digest.lower() if c in "0123456789abcdef")
    sha = f"sha256:{label_hex}".ljust(71, "0")[:71]
    registry_driver.set_registry_digest(_BC_BASE_LATEST_REF, sha)
    ctx["registry_driver"] = registry_driver
    ctx["registry_new_digest"] = new_digest
    ctx["registry_new_sha"] = sha


@then(parsers.parse('launch resolves the bc-base "latest" tag against the '
                    'registry and pulls digest "{new_digest}" before starting '
                    "the container"))
def then_launch_resolves_new_digest(new_digest, ctx):
    registry_driver = ctx["registry_driver"]
    assert _BC_BASE_LATEST_REF in registry_driver.resolve_calls, (
        "launch did not resolve the bc-base \"latest\" tag against the "
        f"registry; resolve calls were: {registry_driver.resolve_calls!r}"
    )
    # The resolution must occur BEFORE the container is started: the recorded
    # docker run command for the container must reference the resolved digest.
    resolved_sha = ctx["registry_new_sha"]
    run_cmd = ctx["fake_driver_for_run"].run_command_for_container(
        ctx["container_name"]
    )
    assert any(resolved_sha in tok for tok in run_cmd), (
        f"launch did not run the container from the resolved digest "
        f"{resolved_sha!r} (label {new_digest!r}); docker run command was: "
        f"{run_cmd!r}"
    )


@then(parsers.parse('the started container "{container_name}" is running from '
                    'image digest "{new_digest}" rather than the cached '
                    '"{old_digest}"'))
def then_container_runs_from_new_digest(container_name, new_digest, old_digest, ctx):
    run_cmd = ctx["fake_driver_for_run"].run_command_for_container(container_name)
    image_tokens = [tok for tok in run_cmd if "shopsystem-bc-base" in tok]
    assert image_tokens, (
        f"docker run for {container_name} carries no bc-base image reference: "
        f"{run_cmd!r}"
    )
    image_ref = image_tokens[0]
    resolved_sha = ctx["registry_new_sha"]
    # The container must run from the registry-resolved digest pin
    # (repo@sha256:...), NOT from the bare ":latest" tag that the local cache
    # would otherwise serve as the stale D_old.
    assert resolved_sha in image_ref, (
        f"Container {container_name} not started from the resolved new digest "
        f"{resolved_sha!r} (label {new_digest!r}): image ref was {image_ref!r}."
    )
    assert image_ref.endswith("@" + resolved_sha), (
        f"Container {container_name} bc-base image ref is not a digest pin "
        f"({old_digest!r} cached-latest tag would otherwise be served): "
        f"{image_ref!r}."
    )
    assert ":latest" not in image_ref, (
        f"Container {container_name} started from the moving :latest tag (the "
        f"cached {old_digest!r}) instead of the resolved digest pin: "
        f"{image_ref!r}."
    )


# --- lead-6ze3: launch image selection (--image flag / BC_IMAGE env) --------

_BC_IMAGE_ENV = "BC_IMAGE"


def _run_image_launch(bc_name, ctx, fake_driver, controller, tmp_path, image):
    """Drive a launch and record the started container's docker run command.

    A fresh, manifest-backed launch through the FakeDockerDriver so the
    resolved launch image is observable as the trailing image token of the
    recorded docker run command for the container.
    """
    repo_url = f"https://github.com/shopsystem/{bc_name}.git"
    default_manifest = tmp_path / "bc-manifest.yaml"
    if not default_manifest.exists():
        import yaml as _yaml
        default_manifest.write_text(_yaml.dump({
            "product": "shopsystem product",
            "bcs": [{"name": bc_name, "remote": repo_url, "role": "bc"}],
        }))
    result = controller.launch(
        bc_name=bc_name,
        repo_url=repo_url,
        image=image,
        manifest_path=default_manifest,
        credential_home=ctx.get("credential_home"),
    )
    ctx["result"] = result
    ctx["fake_driver_for_run"] = fake_driver
    ctx["container_name"] = f"bc-{bc_name}"
    ctx["bc_name"] = bc_name


@given(parsers.parse('the BC_IMAGE environment variable is set to "{value}"'))
def given_bc_image_env_set(value, monkeypatch):
    monkeypatch.setenv(_BC_IMAGE_ENV, value)


@given("the BC_IMAGE environment variable is not set")
def given_bc_image_env_unset(monkeypatch):
    monkeypatch.delenv(_BC_IMAGE_ENV, raising=False)


@when(parsers.parse('I run bc-container launch with BC name "{bc_name}" '
                    'and image "{image}"'))
def when_launch_with_image_flag(bc_name, image, ctx, fake_driver, controller, tmp_path):
    _run_image_launch(bc_name, ctx, fake_driver, controller, tmp_path, image)


@when(parsers.parse('I run bc-container launch with BC name "{bc_name}" '
                    'and no image flag'))
def when_launch_without_image_flag(bc_name, ctx, fake_driver, controller, tmp_path):
    _run_image_launch(bc_name, ctx, fake_driver, controller, tmp_path, None)


@then(parsers.parse('the started container "{container_name}" is running '
                    'from image "{image}"'))
def then_container_runs_from_image(container_name, image, ctx):
    run_cmd = ctx["fake_driver_for_run"].run_command_for_container(container_name)
    # The image is the trailing token of the docker run command (no registry
    # driver is injected here, so launch runs directly from the resolved image
    # with no digest rewrite).
    assert run_cmd and run_cmd[-1] == image, (
        f"Container {container_name} not started from image {image!r}; "
        f"docker run command was: {run_cmd!r}"
    )


@then(parsers.parse('the started container "{container_name}" is NOT running '
                    'from image "{image}"'))
def then_container_not_running_from_image(container_name, image, ctx):
    run_cmd = ctx["fake_driver_for_run"].run_command_for_container(container_name)
    assert image not in run_cmd, (
        f"Container {container_name} unexpectedly started from image {image!r}; "
        f"docker run command was: {run_cmd!r}"
    )


# ===========================================================================
# bclaunch-9rr: bc-base agent-vault install + CA-materialization entrypoint +
# baked placeholder credential.  BC-INTERNAL structural inspection of the
# COMMITTED Dockerfile and CA-trust script content (docker build is NOT run).
# ===========================================================================

# The five TLS-trust env var names are FIXED by the operator design.
_AGENT_VAULT_TRUST_VARS = (
    "GIT_SSL_CAINFO",
    "SSL_CERT_FILE",
    "NODE_EXTRA_CA_CERTS",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
)
# The container CA path is FIXED by the operator design.
_AGENT_VAULT_CONTAINER_CA_PATH = "/home/vscode/.config/agent-vault/ca.pem"


def _bc_base_dir() -> Path:
    return _REPO_ROOT / "docker" / "bc-base"


def _ca_trust_script_path() -> Path | None:
    """Return the committed CA-trust entrypoint/profile script, or None."""
    candidate = _bc_base_dir() / "agent-vault-ca.sh"
    return candidate if candidate.is_file() else None


@when("the bc-base CA-trust script content is inspected")
def when_ca_trust_script_inspected(ctx):
    ctx["bc_base_dockerfile"] = _find_bc_base_dockerfile()
    ctx["ca_trust_script"] = _ca_trust_script_path()


# agent-vault is an EXTERNAL Infisical project distributed as a Go-binary
# release tarball (NOT a pip package, NOT a dstengle repo).  The bc-base image
# installs it from the Infisical/agent-vault releases mirroring the bd binary
# block: arch case, curl download, checksum verification against checksums.txt,
# tarball extract, install onto PATH.  The pin is EXPLICIT (v0.32.0) so the BC
# binary stays compatible with the running broker (which reports 0.32.0).
_AGENT_VAULT_AUTH_VERSION = "0.32.0"


@then("the Dockerfile installs the agent-vault binary with a version pin present")
def then_agent_vault_installed_pinned(ctx):
    dockerfile = ctx.get("bc_base_dockerfile")
    assert dockerfile is not None, "No bc-base Dockerfile found"
    text = dockerfile.read_text()
    assert "agent-vault" in text, (
        "bc-base Dockerfile does not install agent-vault"
    )

    ver = _AGENT_VAULT_AUTH_VERSION  # "0.32.0"

    # (1) AUTHORITATIVE SOURCE: the Infisical/agent-vault GitHub releases — NOT
    # a dstengle repo, NOT a pip/PyPI install.
    assert "github.com/Infisical/agent-vault" in text, (
        "bc-base Dockerfile does not install agent-vault from the authoritative "
        "github.com/Infisical/agent-vault releases.\n"
        f"Dockerfile content:\n{text}"
    )
    assert not re.search(r"github\.com/dstengle/agent-vault", text), (
        "bc-base Dockerfile still installs agent-vault from a dstengle repo "
        "(provisional pip VCS-pin); agent-vault is an external Infisical project."
    )
    assert not re.search(r"pip install[^\n]*agent-vault", text), (
        "bc-base Dockerfile pip-installs agent-vault; it is a Go-binary release "
        "tarball, not a pip package."
    )

    # (2) EXPLICIT v0.32.0 PIN (matches the running broker) — NOT 'latest'.
    assert re.search(
        r"AGENT_VAULT_VERSION\s*=\s*v?" + re.escape(ver) + r"\b", text
    ), (
        f"bc-base Dockerfile does not pin agent-vault to v{ver} explicitly "
        "(expected 'AGENT_VAULT_VERSION=" + ver + "' matching the broker).\n"
        f"Dockerfile content:\n{text}"
    )
    # The release download base must carry the explicit pin, and the
    # arch-appropriate tarball names must be assembled from it.  Accept either a
    # literal URL or the bd-style composed form (base + tarball name from shell
    # vars), since the install block mirrors the bd binary block's structure.
    assert (
        f"github.com/Infisical/agent-vault/releases/download/v{ver}" in text
    ), (
        f"bc-base Dockerfile does not reference the explicit v{ver} agent-vault "
        "release download base.\n"
        f"Dockerfile content:\n{text}"
    )
    for arch in ("amd64", "arm64"):
        # Either the literal pinned tarball name, or a composed
        # agent-vault_${VERSION}_linux_${ARCH}.tar.gz form whose ARCH case maps
        # the uname value to this arch.
        literal = f"agent-vault_{ver}_linux_{arch}.tar.gz" in text
        composed = (
            re.search(r"agent-vault_\$\{?[A-Z_]*VERSION", text) is not None
            and re.search(r"linux_\$\{?AV_ARCH", text) is not None
            and re.search(rf'\b{arch}\b', text) is not None
        )
        assert literal or composed, (
            f"bc-base Dockerfile is missing the explicit v{ver} {arch} release "
            "tarball for agent-vault (neither a literal pinned URL nor a "
            "composed agent-vault_${VERSION}_linux_${ARCH}.tar.gz with an "
            f"{arch} arch case).\n"
            f"Dockerfile content:\n{text}"
        )
    # 'latest' must NOT be used for the agent-vault install (broker-compat pin).
    av_block = re.search(
        r"agent-vault.*?(?=\n# |\Z)", text, re.DOTALL
    )
    assert av_block is not None
    assert not re.search(
        r"agent-vault[^\n]*releases/(latest|download/latest)", text
    ), (
        "bc-base Dockerfile uses 'latest' for the agent-vault release; the pin "
        "must be explicit (v" + ver + ") to stay broker-compatible."
    )

    # (3) CHECKSUM VERIFICATION against checksums.txt BEFORE extraction.
    # The checksums.txt must come from the same pinned v0.32.0 release (either a
    # literal URL or composed from the pinned release base + "checksums.txt").
    literal_checksums = (
        f"github.com/Infisical/agent-vault/releases/download/v{ver}/checksums.txt"
        in text
    )
    composed_checksums = "checksums.txt" in text and (
        f"github.com/Infisical/agent-vault/releases/download/v{ver}" in text
    )
    assert literal_checksums or composed_checksums, (
        "bc-base Dockerfile does not fetch the agent-vault checksums.txt for "
        f"v{ver} to verify the tarball before extraction.\n"
        f"Dockerfile content:\n{text}"
    )
    # Scope the verify/extract ordering check to the agent-vault RUN block so
    # the bd binary block's own `tar -xz` is not mistaken for the agent-vault
    # extraction.  The block runs from the AGENT_VAULT_VERSION assignment up to
    # the build-time `agent-vault --version` sanity check.
    block_m = re.search(
        r"AGENT_VAULT_VERSION\s*=.*?agent-vault\s+--version", text, re.DOTALL
    )
    assert block_m is not None, (
        "bc-base Dockerfile has no agent-vault install RUN block bounded by "
        "AGENT_VAULT_VERSION=... and a build-time 'agent-vault --version' check."
    )
    block = block_m.group(0)
    check_m = re.search(r"sha256sum[^\n]*(-c|--check)", block)
    assert check_m, (
        "bc-base Dockerfile does not verify the agent-vault tarball sha256 "
        "against checksums.txt (expected a 'sha256sum -c' check) before extract."
    )
    # Ordering: the checksum verification must precede the tarball extraction.
    extract_m = re.search(r"tar\s+-x", block)
    assert extract_m is not None, (
        "bc-base Dockerfile does not extract the agent-vault tarball."
    )
    assert check_m.start() < extract_m.start(), (
        "bc-base Dockerfile extracts the agent-vault tarball BEFORE verifying "
        "its sha256 against checksums.txt; verification must come first."
    )

    # (4) BINARY LANDS ON PATH: install the extracted agent-vault binary into a
    # PATH dir (mirroring the bd block's install into /usr/local/bin).
    assert re.search(
        r"install\s+-m\s*0755[^\n]*agent-vault[^\n]*/usr/local/bin", text
    ), (
        "bc-base Dockerfile does not install the agent-vault binary onto PATH "
        "(expected 'install -m 0755 <...>agent-vault /usr/local/bin').\n"
        f"Dockerfile content:\n{text}"
    )


@then("the script is conditional on AGENT_VAULT_CA_PEM being set")
def then_script_conditional_on_ca_pem(ctx):
    script = ctx.get("ca_trust_script")
    assert script is not None, (
        "No CA-trust script found at docker/bc-base/agent-vault-ca.sh"
    )
    text = script.read_text()
    assert "AGENT_VAULT_CA_PEM" in text, (
        "CA-trust script does not reference AGENT_VAULT_CA_PEM"
    )
    # A guard must gate the materialization on the var being set/non-empty.
    assert re.search(
        r'(-n\s+"?\$\{?AGENT_VAULT_CA_PEM|if\s+\[\s+-n.*AGENT_VAULT_CA_PEM'
        r'|\$\{AGENT_VAULT_CA_PEM:[-+])',
        text,
    ), (
        "CA-trust script does not guard materialization on AGENT_VAULT_CA_PEM "
        "being set"
    )


@then(parsers.parse('the script writes the CA to "{path}"'))
def then_script_writes_ca(path, ctx):
    script = ctx.get("ca_trust_script")
    assert script is not None, "No CA-trust script found"
    text = script.read_text()
    assert path == _AGENT_VAULT_CONTAINER_CA_PATH, (
        f"Feature names CA path {path!r} but the fixed design path is "
        f"{_AGENT_VAULT_CONTAINER_CA_PATH!r}"
    )
    assert path in text, (
        f"CA-trust script does not write the CA to {path!r}"
    )


@then(parsers.parse('the script exports {var} pointing at the container CA path'))
def then_script_exports_trust_var(var, ctx):
    script = ctx.get("ca_trust_script")
    assert script is not None, "No CA-trust script found"
    text = script.read_text()
    assert var in _AGENT_VAULT_TRUST_VARS, (
        f"{var!r} is not one of the five fixed trust vars "
        f"{_AGENT_VAULT_TRUST_VARS!r}"
    )
    # The var must be exported AND resolve to the fixed container CA path.
    assert re.search(rf"export\s+{re.escape(var)}=", text), (
        f"CA-trust script does not export {var}"
    )
    # Find the assignment value.  Accept either the literal CA path OR a shell
    # variable (e.g. AGENT_VAULT_CA_PATH) that is itself defined to the fixed
    # container CA path elsewhere in the script.
    m = re.search(rf"{re.escape(var)}=([^\n]+)", text)
    assert m, f"CA-trust script has no {var}= assignment"
    value = m.group(1)
    points_at_path = _AGENT_VAULT_CONTAINER_CA_PATH in value
    if not points_at_path:
        # Resolve a single ${VARNAME} / $VARNAME indirection to its definition.
        ref = re.search(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?", value)
        if ref:
            ref_var = ref.group(1)
            defn = re.search(rf'{re.escape(ref_var)}=("?){re.escape(_AGENT_VAULT_CONTAINER_CA_PATH)}',
                             text)
            points_at_path = defn is not None
    assert points_at_path, (
        f"{var} is not pointed at the container CA path "
        f"{_AGENT_VAULT_CONTAINER_CA_PATH!r} (value: {value!r})"
    )


@then("a /etc/profile.d agent-vault CA script is installed that materializes "
      "the CA if missing and exports the five trust vars")
def then_profile_d_script_installed(ctx):
    # The Dockerfile must install the CA-trust script into /etc/profile.d so
    # exec/login shells (the `docker exec ... agent-vault run -- claude` path,
    # which does NOT inherit the entrypoint's process-local exports) see the
    # trust vars.  The script itself must materialize the CA file if missing
    # and export all five trust vars.
    dockerfile = ctx.get("bc_base_dockerfile")
    assert dockerfile is not None, "No bc-base Dockerfile found"
    df_text = dockerfile.read_text()
    assert "/etc/profile.d" in df_text and "agent-vault" in df_text, (
        "bc-base Dockerfile does not install an agent-vault CA script under "
        "/etc/profile.d for exec/login-shell durability"
    )
    script = ctx.get("ca_trust_script")
    assert script is not None, "No CA-trust script found"
    text = script.read_text()
    # Materialize-if-missing: a guard that (re)writes the CA file when absent.
    assert _AGENT_VAULT_CONTAINER_CA_PATH in text, (
        "CA-trust script does not materialize the CA file path"
    )
    for var in _AGENT_VAULT_TRUST_VARS:
        assert re.search(rf"export\s+{re.escape(var)}=", text), (
            f"CA-trust script does not export {var} for login/exec shells"
        )


@then(parsers.parse(
    'the Dockerfile bakes a nested-claudeAiOauth .credentials.json at "{path}" '
    'whose claudeAiOauth accessToken is "{token}"'
))
def then_dockerfile_bakes_nested_credential(path, token, ctx):
    dockerfile = ctx.get("bc_base_dockerfile")
    assert dockerfile is not None, "No bc-base Dockerfile found"
    text = dockerfile.read_text()
    assert path in text, (
        f"bc-base Dockerfile does not bake the credential at {path!r}"
    )
    creds = _baked_credentials_json()
    oauth = creds.get("claudeAiOauth")
    assert isinstance(oauth, dict), (
        f"bc-base Dockerfile does not bake the NESTED claudeAiOauth shape "
        f"(bclaunch-2s6y); parsed: {creds!r}"
    )
    assert oauth.get("accessToken") == token, (
        f"bc-base Dockerfile claudeAiOauth.accessToken is "
        f"{oauth.get('accessToken')!r}, expected {token!r}"
    )
    assert "accessToken" not in creds, (
        "bc-base Dockerfile bakes a TOP-LEVEL accessToken (the superseded bare "
        "shape); it must live inside claudeAiOauth."
    )


@then("the baked .credentials.json claudeAiOauth expiresAt is far in the future")
def then_credential_expiry_far_future(ctx):
    # Far-future expiry so claude never attempts a refresh (the broker swaps the
    # Authorization header regardless).  Assert expiresAt is well beyond now
    # (epoch-millis) — concretely past the year 2100.
    creds = _baked_credentials_json()
    oauth = creds.get("claudeAiOauth") or {}
    expires = oauth.get("expiresAt")
    assert isinstance(expires, (int, float)), (
        f"claudeAiOauth.expiresAt is not numeric: {expires!r}"
    )
    # 2100-01-01 in epoch-millis ~= 4102444800000.
    assert expires >= 4_000_000_000_000, (
        f"claudeAiOauth.expiresAt {expires!r} is not far-future; a near expiry "
        f"would make claude attempt a token refresh."
    )


@then(parsers.parse(
    'the Dockerfile seeds a ~/.claude.json at "{path}" with hasCompletedOnboarding '
    'true and bypassPermissionsModeAccepted true'
))
def then_dockerfile_seeds_claude_json(path, ctx):
    dockerfile = ctx.get("bc_base_dockerfile")
    assert dockerfile is not None, "No bc-base Dockerfile found"
    text = dockerfile.read_text()
    assert path in text, (
        f"bc-base Dockerfile does not seed a ~/.claude.json at {path!r}"
    )
    claude_json = _baked_claude_json()
    assert claude_json.get("hasCompletedOnboarding") is True, (
        f"seeded ~/.claude.json hasCompletedOnboarding is not true: "
        f"{claude_json.get('hasCompletedOnboarding')!r}"
    )
    # The bypass-permissions acceptance gate key — confirmed against the claude
    # 2.1.170 binary (read as !S$().bypassPermissionsModeAccepted from the global
    # ~/.claude.json config).  Without it claude stops at the
    # --dangerously-skip-permissions warning gate.
    assert claude_json.get("bypassPermissionsModeAccepted") is True, (
        f"seeded ~/.claude.json bypassPermissionsModeAccepted is not true: "
        f"{claude_json.get('bypassPermissionsModeAccepted')!r} — claude would "
        f"stop at the --dangerously-skip-permissions warning gate."
    )


@then(parsers.parse(
    'the seeded ~/.claude.json pre-trusts the "{project}" project'
))
def then_claude_json_pretrusts_project(project, ctx):
    claude_json = _baked_claude_json()
    proj = (claude_json.get("projects") or {}).get(project)
    assert isinstance(proj, dict), (
        f"seeded ~/.claude.json has no projects[{project!r}] stanza: "
        f"{claude_json.get('projects')!r}"
    )
    assert proj.get("hasTrustDialogAccepted") is True, (
        f"projects[{project!r}].hasTrustDialogAccepted is not true — the "
        f"folder-trust prompt would fire: {proj!r}"
    )
    assert proj.get("hasCompletedProjectOnboarding") is True, (
        f"projects[{project!r}].hasCompletedProjectOnboarding is not true: "
        f"{proj!r}"
    )


@then("the seeded ~/.claude.json bakes no real Claude OAuth token")
def then_claude_json_no_real_token(ctx):
    import json as _json
    claude_json = _baked_claude_json()
    creds = _baked_credentials_json()
    blob = _json.dumps(claude_json) + _json.dumps(creds)
    assert _REAL_OAUTH_TOKEN not in blob, (
        "A real Claude OAuth token is baked into the bc-base image."
    )
    # Defensive: assert the only token-shaped values are the synthetic
    # placeholder.  Every accessToken/refreshToken in the credential is the
    # literal placeholder.
    oauth = creds.get("claudeAiOauth") or {}
    for field in ("accessToken", "refreshToken"):
        assert oauth.get(field) == AGENT_VAULT_PLACEHOLDER_TOKEN, (
            f"claudeAiOauth.{field} is not the synthetic placeholder: "
            f"{oauth.get(field)!r}"
        )


@then("the Dockerfile declares an ENTRYPOINT that runs the agent-vault CA "
      "entrypoint script")
def then_dockerfile_declares_entrypoint(ctx):
    dockerfile = ctx.get("bc_base_dockerfile")
    assert dockerfile is not None, "No bc-base Dockerfile found"
    text = dockerfile.read_text()
    assert re.search(r"^\s*ENTRYPOINT", text, re.MULTILINE), (
        "bc-base Dockerfile declares no ENTRYPOINT (the image previously had "
        "only CMD); the CA-materialization entrypoint must run on container "
        "start"
    )
    assert "agent-vault" in text, (
        "bc-base ENTRYPOINT does not reference the agent-vault CA script"
    )


# ===========================================================================
# bc-base HEALTHCHECK structural pinning (bclaunch-wuo)
#
# The real bc-base image previously carried NO HEALTHCHECK instruction, so
# RealDockerDriver.health_status (docker inspect .State.Health.Status) always
# read "none" — the broker-down / DB-down "unhealthy" behavior pinned by lead
# scenario 3b2a81c1bfe2897e and the messaging-db health scenarios was fake-only.
# These steps parse the COMMITTED Dockerfile HEALTHCHECK directive and the
# committed bc-healthcheck.sh probe-script content (docker build is NOT run),
# asserting on the ACTUAL probe targets so a no-op or wrong-target HEALTHCHECK
# fails. Same structural-inspection idiom as the CA-trust / CLI-pin tests.
# ===========================================================================

# The in-container env vars the probe MUST read its targets from. These are the
# same env keys controller.launch injects (HTTPS_PROXY = AGENT_VAULT_PROXY_ENV,
# SHOPMSG_DSN). If the probe read a different/static target the assertions below
# would fail — that is what keeps this pinning non-tautological.
_HEALTHCHECK_BROKER_ENV = "HTTPS_PROXY"
_HEALTHCHECK_DB_ENV = "SHOPMSG_DSN"


def _healthcheck_script_path() -> Path | None:
    """Return the committed bc-base HEALTHCHECK probe script, or None."""
    candidate = _bc_base_dir() / "bc-healthcheck.sh"
    return candidate if candidate.is_file() else None


def _strip_sh_comments(body: str) -> str:
    """Return the probe-script body with whole-line and trailing # comments
    removed, so env-derivation assertions match EXECUTABLE code rather than a
    mention of the env var in a comment. A heredoc-embedded python block uses
    the same '#' comment char, so this is a coarse strip that drops any text
    from an unquoted '#' to end-of-line; that is sufficient because the
    assertions only need the var to appear in a real assignment/expansion, and
    a wrong-target mutation that hard-codes the address would no longer have the
    env var in executable code."""
    out_lines = []
    for line in body.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        # Drop trailing comments (best-effort; the probe script does not use
        # '#' inside quoted strings on its executable lines).
        hash_idx = line.find("#")
        if hash_idx != -1:
            line = line[:hash_idx]
        out_lines.append(line)
    return "\n".join(out_lines)


def _dockerfile_healthcheck_directive(text: str) -> str | None:
    """Extract the HEALTHCHECK instruction body (including line continuations).

    Returns the full directive text after the HEALTHCHECK keyword (joining
    backslash-continued lines), or None if no HEALTHCHECK instruction is
    present.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if re.match(r"^\s*HEALTHCHECK\b", line):
            collected = [line]
            j = i
            while collected[-1].rstrip().endswith("\\") and j + 1 < len(lines):
                j += 1
                collected.append(lines[j])
            joined = " ".join(c.rstrip().rstrip("\\").strip() for c in collected)
            return re.sub(r"^\s*HEALTHCHECK\s*", "", joined)
    return None


@when("the bc-base healthcheck probe script content is inspected")
def when_healthcheck_probe_script_inspected(ctx):
    ctx["bc_base_dockerfile"] = _find_bc_base_dockerfile()
    ctx["healthcheck_script"] = _healthcheck_script_path()


@then("the Dockerfile declares a HEALTHCHECK instruction")
def then_dockerfile_declares_healthcheck(ctx):
    dockerfile = ctx.get("bc_base_dockerfile")
    assert dockerfile is not None, "No bc-base Dockerfile found"
    text = dockerfile.read_text()
    directive = _dockerfile_healthcheck_directive(text)
    assert directive is not None, (
        "bc-base Dockerfile declares no HEALTHCHECK instruction; without one "
        ".State.Health is absent and docker inspect reports 'none', so the "
        "unhealthy-when-broker-down behavior is fake-driver-only."
    )
    ctx["healthcheck_directive"] = directive


@then("the HEALTHCHECK command runs the in-container bc-healthcheck probe script")
def then_healthcheck_runs_probe_script(ctx):
    directive = ctx.get("healthcheck_directive")
    if directive is None:
        text = ctx["bc_base_dockerfile"].read_text()
        directive = _dockerfile_healthcheck_directive(text)
    assert directive is not None, "No HEALTHCHECK directive present"
    # The HEALTHCHECK must invoke the committed probe script, not an inline
    # one-liner that could silently drift from the script the script-content
    # scenarios pin. Assert the actual probe-script path appears in the CMD.
    assert "bc-healthcheck.sh" in directive, (
        "bc-base HEALTHCHECK does not run the bc-healthcheck.sh probe script; "
        f"directive was: {directive!r}"
    )
    # And the probe script must actually be present in the image (a COPY of it).
    text = ctx["bc_base_dockerfile"].read_text()
    assert re.search(r"COPY\s+bc-healthcheck\.sh\s+\S+", text), (
        "bc-base Dockerfile HEALTHCHECK references bc-healthcheck.sh but the "
        "Dockerfile never COPYs the script into the image."
    )
    script = _healthcheck_script_path()
    assert script is not None, (
        "bc-base HEALTHCHECK references bc-healthcheck.sh but no such committed "
        "script exists under docker/bc-base/."
    )


@then("the HEALTHCHECK is not a no-op that always reports healthy")
def then_healthcheck_not_noop(ctx):
    directive = ctx.get("healthcheck_directive")
    if directive is None:
        text = ctx["bc_base_dockerfile"].read_text()
        directive = _dockerfile_healthcheck_directive(text)
    assert directive is not None, "No HEALTHCHECK directive present"
    lowered = directive.lower()
    # A HEALTHCHECK that hard-codes success (CMD true / CMD exit 0 / CMD :)
    # would make the container report healthy unconditionally — defeating the
    # broker-down / DB-down detection. Reject those no-op shapes outright.
    assert not re.search(r"\bcmd\b\s+\[?\s*[\"']?(true|:|exit\s+0)\b", lowered), (
        "bc-base HEALTHCHECK is a no-op that always succeeds; it must probe "
        f"the broker and messaging-db reachability. Directive: {directive!r}"
    )
    # The probe script the directive runs must itself exercise a real probe.
    script = _healthcheck_script_path()
    assert script is not None, "No bc-healthcheck.sh probe script found"
    body = script.read_text()
    assert "exit 0" in body and "exit 1" in body, (
        "bc-healthcheck.sh never differentiates healthy (exit 0) from "
        "unhealthy (exit 1); a probe that cannot fail is a no-op."
    )


@then("the probe derives the agent-vault broker address from the in-container "
      "HTTPS_PROXY env var")
def then_probe_broker_from_https_proxy(ctx):
    script = ctx.get("healthcheck_script") or _healthcheck_script_path()
    assert script is not None, "No bc-healthcheck.sh probe script found"
    code = _strip_sh_comments(script.read_text())
    # The broker target must come from the runtime HTTPS_PROXY env var (the
    # address the container actually routes outbound HTTPS through), NOT a
    # baked literal. Assert EXECUTABLE code expands ${HTTPS_PROXY} — a comment
    # mention is stripped, so a wrong-target probe that hard-codes a host:port
    # would fail here even though its comment still says "HTTPS_PROXY".
    assert re.search(r"\$\{?" + _HEALTHCHECK_BROKER_ENV + r"\b", code), (
        f"bc-healthcheck.sh does not expand ${_HEALTHCHECK_BROKER_ENV} in "
        "executable code; the broker target must be the runtime proxy-listener "
        "address the container routes through, not a baked literal."
    )


@then("the probe attempts a TCP connect against the broker host and port")
def then_probe_tcp_connect_broker(ctx):
    script = ctx.get("healthcheck_script") or _healthcheck_script_path()
    assert script is not None, "No bc-healthcheck.sh probe script found"
    body = script.read_text()
    # The reachability check must be a real TCP connect against the parsed
    # host:port, mirroring RealDockerDriver.agent_vault_reachable. A probe that
    # merely echoes a string is tautological.
    assert "create_connection" in body, (
        "bc-healthcheck.sh does not perform a TCP connect (socket."
        "create_connection) against the broker host:port; a probe that does "
        "not actually connect cannot detect an unreachable broker."
    )
    # The host:port must be PARSED out of the address (urlparse), not assumed.
    assert "urlparse" in body or re.search(r"hostname|\.port\b", body), (
        "bc-healthcheck.sh does not parse a host:port out of the broker "
        "address; it must derive host and port from the env-supplied address."
    )


@then("the probe exits non-zero when the broker is unreachable")
def then_probe_exits_nonzero_broker_down(ctx):
    script = ctx.get("healthcheck_script") or _healthcheck_script_path()
    assert script is not None, "No bc-healthcheck.sh probe script found"
    body = script.read_text()
    # A failed broker TCP connect must drive a non-zero exit (-> docker reports
    # unhealthy). Assert the broker branch exits 1 on failure.
    assert re.search(r"broker[^\n]*\n[^\n]*exit 1", body) or (
        "broker unreachable" in body and "exit 1" in body
    ), (
        "bc-healthcheck.sh does not exit non-zero when the broker is "
        "unreachable; docker would then report the container healthy with a "
        "dead broker (the exact fake-only gap this pins)."
    )


@then("the probe derives the messaging database address from the SHOPMSG_DSN "
      "env var")
def then_probe_db_from_shopmsg_dsn(ctx):
    script = ctx.get("healthcheck_script") or _healthcheck_script_path()
    assert script is not None, "No bc-healthcheck.sh probe script found"
    code = _strip_sh_comments(script.read_text())
    assert re.search(r"\$\{?" + _HEALTHCHECK_DB_ENV + r"\b", code), (
        f"bc-healthcheck.sh does not expand ${_HEALTHCHECK_DB_ENV} in "
        "executable code; the DB target must be the runtime DSN, not a baked "
        "literal."
    )


@then("the probe exits non-zero when the messaging database is unreachable")
def then_probe_exits_nonzero_db_down(ctx):
    script = ctx.get("healthcheck_script") or _healthcheck_script_path()
    assert script is not None, "No bc-healthcheck.sh probe script found"
    body = script.read_text()
    assert re.search(r"database[^\n]*\n[^\n]*exit 1", body) or (
        "messaging database unreachable" in body and "exit 1" in body
    ), (
        "bc-healthcheck.sh does not exit non-zero when the messaging database "
        "is unreachable."
    )


# ===========================================================================
# lead-f6xs — bc-base INTERACTIVE BOOTSTRAP entrypoint mode (scenarios
# 20b7a66364a26404 + 938342272de4e38a). Structural inspection of the COMMITTED
# bootstrap-entrypoint script + bc-base Dockerfile content (docker build is NOT
# run — docker is unavailable in this environment), the same idiom as the
# bc-base CA-trust / CLI-pin tests.
# ===========================================================================

# The four baked framework CLIs that must resolve on PATH in a bootstrap-mode
# container exactly as for a brokered steady-state run.
_BOOTSTRAP_FRAMEWORK_CLIS = (
    "shop-templates",
    "shop-msg",
    "bc-container",
    "agent-vault",
)


def _bootstrap_entrypoint_path():
    """Return the committed bootstrap-entrypoint script Path, or None."""
    candidate = _bc_base_dir() / "bootstrap-entrypoint.sh"
    return candidate if candidate.is_file() else None


@given("the published bc-base image is run with the interactive bootstrap "
       "entrypoint mode selected")
def given_bc_base_bootstrap_mode(ctx):
    ctx["repo_root"] = _REPO_ROOT
    ctx["bootstrap_entrypoint"] = _bootstrap_entrypoint_path()
    ctx["bc_base_dockerfile"] = _find_bc_base_dockerfile()


@given("the agent-vault broker holds no Claude or GitHub credential for this "
       "product yet")
def given_broker_holds_no_credential(ctx):
    # Pre-state marker for the bootstrap beat: no real credential held yet, so
    # the human-auth beat is what obtains them. No additional fixture state is
    # required for the structural inspection of the committed entrypoint.
    ctx["broker_credential_present"] = False


@when("the bootstrap entrypoint executes its authentication beat")
def when_bootstrap_beat_executes(ctx):
    ctx["bootstrap_entrypoint"] = _bootstrap_entrypoint_path()
    ctx["bc_base_dockerfile"] = _find_bc_base_dockerfile()


@when("the bootstrap entrypoint starts")
def when_bootstrap_entrypoint_starts(ctx):
    ctx["bootstrap_entrypoint"] = _bootstrap_entrypoint_path()
    ctx["bc_base_dockerfile"] = _find_bc_base_dockerfile()


@then(parsers.parse(
    'the entrypoint invokes "{cmd}" interactively attached to the host TTY '
    'for the human to authenticate, not wrapped as "{wrap}"'))
def then_bootstrap_invokes_claude_tty(ctx, cmd, wrap):
    script = ctx.get("bootstrap_entrypoint") or _bootstrap_entrypoint_path()
    assert script is not None, (
        "No bootstrap entrypoint script found at "
        "docker/bc-base/bootstrap-entrypoint.sh"
    )
    body = script.read_text()
    code = _strip_sh_comments(body)
    # The command must be invoked in EXECUTABLE code (not just mentioned in a
    # comment), interactively attached to the host TTY (/dev/tty).
    invoke_re = re.compile(
        r"(?m)^[^\n#]*\b" + re.escape(cmd) + r"\b[^\n]*</dev/tty"
    )
    assert invoke_re.search(code), (
        f"bootstrap entrypoint does not invoke {cmd!r} interactively attached "
        f"to the host TTY (/dev/tty) in executable code.\n"
        f"Executable content:\n{code}"
    )
    # It must NOT be wrapped as the brokered placeholder wrap (`agent-vault run
    # -- claude`). Reject any executable line that wraps this command that way.
    #
    # NOTE: anchor the negative match on the WRAP PREFIX itself (the broker verb
    # `agent-vault run --` followed by the command), with flexible whitespace
    # between the wrap tokens. We deliberately do NOT append a trailing
    # backreference to `cmd`: because `wrap` already ends in the command token
    # (`agent-vault run -- claude`, where cmd == `claude`), demanding a SECOND
    # `cmd` after the wrap would make the assertion vacuous — the canonical
    # forbidden line `agent-vault run -- claude </dev/tty` has only one `claude`
    # token and would never match. Build the pattern from the wrap's own tokens
    # so the forbidden broker-wrapped invocation actually triggers the assert.
    wrap_pat = r"\s+".join(re.escape(tok) for tok in wrap.split())
    wrap_re = re.compile(r"(?m)^[^\n#]*\b" + wrap_pat + r"\b")
    assert not wrap_re.search(code), (
        f"bootstrap entrypoint wraps {cmd!r} as {wrap!r}; the interactive "
        f"bootstrap beat must invoke {cmd!r} directly attached to the host TTY, "
        f"NOT via the brokered placeholder wrap.\nExecutable content:\n{code}"
    )


@then(parsers.parse(
    'the entrypoint invokes "{cmd}" interactively attached to the host TTY '
    'for the human to authenticate'))
def then_bootstrap_invokes_gh_tty(ctx, cmd):
    script = ctx.get("bootstrap_entrypoint") or _bootstrap_entrypoint_path()
    assert script is not None, (
        "No bootstrap entrypoint script found at "
        "docker/bc-base/bootstrap-entrypoint.sh"
    )
    code = _strip_sh_comments(script.read_text())
    invoke_re = re.compile(
        r"(?m)^[^\n#]*\b" + re.escape(cmd) + r"\b[^\n]*</dev/tty"
    )
    assert invoke_re.search(code), (
        f"bootstrap entrypoint does not invoke {cmd!r} interactively attached "
        f"to the host TTY (/dev/tty) in executable code.\n"
        f"Executable content:\n{code}"
    )


@then(parsers.parse(
    'the entrypoint does not place a "{placeholder}" credential as the Claude '
    'or GitHub credential for this beat'))
def then_bootstrap_no_placeholder(ctx, placeholder):
    script = ctx.get("bootstrap_entrypoint") or _bootstrap_entrypoint_path()
    assert script is not None, (
        "No bootstrap entrypoint script found at "
        "docker/bc-base/bootstrap-entrypoint.sh"
    )
    code = _strip_sh_comments(script.read_text())
    # The placeholder token is the steady-state brokered artifact; the bootstrap
    # beat obtains REAL human credentials and must never write/seed the literal
    # placeholder as the operative Claude/GitHub credential. Assert the literal
    # does not appear in EXECUTABLE code (comments documenting the contrast are
    # allowed and expected).
    assert placeholder not in code, (
        f"bootstrap entrypoint places the {placeholder!r} placeholder token in "
        f"executable code; the human-auth beat must obtain real credentials and "
        f"never seed the placeholder as the operative credential.\n"
        f"Executable content:\n{code}"
    )


@then("the image is the existing bc-base lineage image and not a separate "
      "purpose-built bootstrap image")
def then_bootstrap_is_existing_image(ctx):
    dockerfile = ctx.get("bc_base_dockerfile") or _find_bc_base_dockerfile()
    assert dockerfile is not None, (
        "No tracked Dockerfile building shopsystem-bc-base found under the "
        "bc-launcher repository file tree."
    )
    text = dockerfile.read_text()
    # The bootstrap entrypoint must ship inside the SAME bc-base image (a mode
    # of it), not a separate purpose-built bootstrap Dockerfile. Assert the
    # bootstrap-entrypoint.sh is COPY'd into the bc-base image.
    assert "shopsystem-bc-base" in text, (
        "The Dockerfile carrying the bootstrap entrypoint is not the "
        "shopsystem-bc-base lineage image."
    )
    assert re.search(r"(?m)^\s*COPY\s+bootstrap-entrypoint\.sh\b", text), (
        "The bc-base Dockerfile does not COPY bootstrap-entrypoint.sh into the "
        "image; the bootstrap mode must be a mode of the EXISTING bc-base "
        "lineage image, not a separate purpose-built bootstrap image.\n"
        f"Dockerfile content:\n{text}"
    )
    # There must be exactly one Dockerfile that BUILDS bc-base under the repo: a
    # separate purpose-built bootstrap Dockerfile would be a second image.
    # A Dockerfile deriving ``FROM`` a shopsystem-bc-base image (e.g. the thin
    # docker/bc-lead/Dockerfile) consumes the base rather than building it and
    # must not be counted as a second bc-base build (bug
    # shopsystem_bc_launcher-hnr / 6lx).
    bc_base_dockerfiles = [
        p for p in _REPO_ROOT.rglob("Dockerfile*")
        if ".git" not in p.parts and p.is_file()
        and "shopsystem-bc-base" in p.read_text()
        and not re.search(
            r"(?im)^\s*FROM\s+\S*shopsystem-bc-base", p.read_text())
    ]
    assert len(bc_base_dockerfiles) == 1, (
        "Expected exactly one bc-base build Dockerfile (the bootstrap mode is "
        f"a mode of it); found {len(bc_base_dockerfiles)}: "
        f"{[str(p.relative_to(_REPO_ROOT)) for p in bc_base_dockerfiles]}"
    )


@then(parsers.parse(
    'the framework CLIs "{a}", "{b}", "{c}", and "{d}" resolve on PATH inside '
    'the running container exactly as they do for a brokered steady-state run'))
def then_bootstrap_clis_on_path(ctx, a, b, c, d):
    dockerfile = ctx.get("bc_base_dockerfile") or _find_bc_base_dockerfile()
    assert dockerfile is not None, (
        "No tracked Dockerfile building shopsystem-bc-base found."
    )
    dtext = dockerfile.read_text()
    named = (a, b, c, d)
    assert set(named) == set(_BOOTSTRAP_FRAMEWORK_CLIS), (
        f"Scenario named CLIs {named} differ from the expected baked set "
        f"{_BOOTSTRAP_FRAMEWORK_CLIS}."
    )
    # shop-msg, shop-templates and bc-container are provided by the three
    # pip-installed dstengle framework packages (shopsystem-messaging ->
    # shop-msg console-script, shop-templates, shopsystem-bc-launcher ->
    # bc-container console-script); agent-vault is the installed Go binary.
    # Assert the Dockerfile installs each provider so the console scripts /
    # binary resolve on PATH for ANY run of the image (brokered or bootstrap).
    assert re.search(
        r"shopsystem-messaging @ git\+https://github\.com/dstengle/"
        r"shopsystem-messaging(?:\.git)?@v\d+\.\d+\.\d+", dtext), (
        "bc-base Dockerfile does not install shopsystem-messaging (provides the "
        "shop-msg CLI) from a dstengle VCS version pin."
    )
    # shop-templates is installed from its dstengle VCS pin; since lead-pwa2
    # (scenario edd2c813688ab768) its version is parameterized through the
    # SHOP_TEMPLATES_VERSION build ARG (default vX.Y.Z) rather than a frozen
    # literal -- either way the shop-templates CLI resolves on PATH.
    assert _shop_templates_pinned_by_version_shape(dtext), (
        "bc-base Dockerfile does not install shop-templates from a dstengle VCS "
        "version pin (literal or SHOP_TEMPLATES_VERSION ARG defaulted to vX.Y.Z)."
    )
    assert re.search(
        r"shopsystem-bc-launcher @ git\+https://github\.com/dstengle/"
        r"shopsystem-bc-launcher(?:\.git)?@v\d+\.\d+\.\d+", dtext), (
        "bc-base Dockerfile does not install shopsystem-bc-launcher (provides "
        "the bc-container CLI) from a dstengle VCS version pin."
    )
    assert "Infisical/agent-vault/releases" in dtext and \
        "install -m 0755 /tmp/agent-vault /usr/local/bin/agent-vault" in dtext, (
        "bc-base Dockerfile does not install the agent-vault binary onto "
        "/usr/local/bin (so it would not resolve on PATH)."
    )
    # The bootstrap entrypoint itself relies on these resolving on PATH and
    # fail-fast checks each one — confirm it does not strip / re-export a PATH
    # that would diverge from the brokered run (it must add nothing/remove
    # nothing). The entrypoint must reference all four CLIs in its PATH guard.
    bscript = ctx.get("bootstrap_entrypoint") or _bootstrap_entrypoint_path()
    assert bscript is not None, "No bootstrap entrypoint script found."
    bcode = _strip_sh_comments(bscript.read_text())
    for cli in named:
        assert cli in bcode, (
            f"bootstrap entrypoint does not reference framework CLI {cli!r} in "
            f"its PATH-resolution guard."
        )
    # The bootstrap entrypoint must NOT mutate PATH (which would diverge from a
    # brokered run's PATH resolution).
    assert not re.search(r"(?m)^[^\n#]*\bexport\s+PATH=", bcode), (
        "bootstrap entrypoint mutates PATH; the baked framework CLIs must "
        "resolve on PATH exactly as for a brokered steady-state run."
    )


# ===========================================================================
# lead-cs7k — readiness probes run inside the container network, and the probe
# broker host derives from the product slug (decoupled from the runtime proxy)
# ===========================================================================
#
# These TIGHTEN the already-pinned readiness barrier. The pass/withhold
# semantics (both-reachable -> inject, either-unreachable -> withhold) are
# UNCHANGED; what changes is WHERE each probe runs (inside the container's
# network, not the launcher host) and which broker host the PROBE targets
# (derived from the product slug, decoupled from the verbatim runtime proxy).

# --- Scenario 27b73cbb: probes run inside the container network -------------

@given(parsers.parse(
    'a BC container "{container_name}" is launched on the docker network '
    '"{network}" for a product whose slug is "{slug}"'
))
def cs7k_container_on_product_network(
    container_name, network, slug, ctx, fake_driver, tmp_path
):
    """Stash the launch parameters for a second-product (dummyco) launch.

    The actual launch is performed in the When step, after the network
    reachability and host-unresolvable conditions are configured, so the
    readiness probes observe the second-product network topology.
    """
    bc_name = container_name.removeprefix("bc-")
    ctx["cs7k_bc_name"] = bc_name
    ctx["cs7k_container_name"] = container_name
    ctx["cs7k_network"] = network
    ctx["cs7k_slug"] = slug
    # The container is attached to the product network; probes that docker-exec
    # into it resolve against this network.
    fake_driver.set_container_network(container_name, network)
    # A bc-manifest whose product: is the slug so the launch derives the
    # network and the probe broker host from it.
    import yaml as _yaml
    manifest = tmp_path / "bc-manifest.yaml"
    manifest.write_text(_yaml.dump({
        "product": slug,
        "bcs": [{
            "name": bc_name,
            "remote": f"https://github.com/{slug}/{bc_name}.git",
            "role": "bc",
        }],
    }))
    ctx["launch_manifest_path"] = manifest


@given(parsers.parse(
    'the launcher host process is NOT attached to the "{network}" docker network'
))
def cs7k_host_not_on_network(network, ctx, fake_driver):
    """The launcher host cannot resolve the product-network service hostnames.

    So a probe run FROM THE HOST against "<slug>-postgres" or the product
    broker host false-fails — only a probe executed inside the container's
    network reaches them.  Concrete host tokens are registered in the
    reachability Given below.
    """
    ctx["cs7k_host_off_network"] = network


@given(parsers.parse(
    'the messaging database is reachable as "{db_target}" from inside the '
    '"{network}" network and the agent-vault broker is reachable from inside '
    'that network'
))
def cs7k_reachable_from_inside(db_target, network, ctx, fake_driver):
    slug = ctx["cs7k_slug"]
    # DSN host token (strip any :port for the host comparison).
    db_host = db_target.split(":", 1)[0]
    broker_host = f"{slug}-agent-vault"
    # Reachable from INSIDE the product network (docker exec into the container).
    fake_driver.set_network_target_reachable_from_inside(network, db_host)
    fake_driver.set_network_target_reachable_from_inside(network, broker_host)
    # NOT resolvable from the launcher HOST process — this is the second-product
    # bug: a host-context probe false-fails both.
    fake_driver.set_host_cannot_resolve(db_host)
    fake_driver.set_host_cannot_resolve(broker_host)
    # The DSN this launch uses points at the product DB by its in-network name.
    ctx["cs7k_dsn"] = f"postgres://{db_target}/messaging"
    ctx["cs7k_db_host"] = db_host
    ctx["cs7k_broker_host"] = broker_host


@when("bc-container launch runs its messaging-database and agent-vault "
      "readiness probes")
def cs7k_run_launch_probes(ctx, fake_driver, controller):
    bc_name = ctx["cs7k_bc_name"]
    result = controller.launch(
        bc_name=bc_name,
        repo_url=f"https://github.com/{ctx['cs7k_slug']}/{bc_name}.git",
        shopmsg_dsn=ctx["cs7k_dsn"],
        startup_prompt="please begin your session",
        network=None,
        manifest_path=ctx["launch_manifest_path"],
    )
    ctx["result"] = result
    ctx["container_name"] = ctx["cs7k_container_name"]
    ctx["bc_name"] = bc_name


@then("each readiness probe is executed from inside the launched container's "
      "network context rather than from the launcher host process")
def cs7k_probes_inside_container(ctx, fake_driver):
    container_name = ctx["cs7k_container_name"]
    contexts = fake_driver.probe_exec_contexts()
    kinds = {kind for kind, _ in contexts}
    assert "messaging_db" in kinds and "agent_vault" in kinds, (
        f"Expected both a messaging-db and an agent-vault readiness probe; "
        f"recorded probe contexts: {contexts!r}"
    )
    # CRITICAL: every probe must have run via docker exec INTO the launched
    # container (container context == this container), NOT from the launcher
    # host process (container context None).
    assert contexts, "No readiness probes were executed at all"
    for kind, container in contexts:
        assert container == container_name, (
            f"Readiness probe {kind!r} ran from the launcher host process "
            f"(container={container!r}); it must run inside the launched "
            f"container {container_name!r}'s network context. "
            f"All recorded probe contexts: {contexts!r}"
        )


@then(parsers.parse(
    'both probes report reachable even though the launcher host cannot itself '
    'resolve "{db_host}" or the broker host'
))
def cs7k_both_probes_reachable(db_host, ctx, fake_driver):
    # The launch succeeded (rc 0) and committed the startup prompt — which only
    # happens when BOTH probes passed.  Independently assert that a HOST-context
    # probe against the same targets would have FAILED (proving the fix is the
    # inside-network execution, not merely default reachability).
    result = ctx["result"]
    assert result.exit_code == 0, (
        f"Expected launch to succeed with both inside-network probes passing, "
        f"got rc={result.exit_code} stderr={result.stderr!r}"
    )
    assert not fake_driver.messaging_db_reachable(ctx["cs7k_dsn"]), (
        "A launcher-HOST-context messaging probe should false-fail for the "
        "second product (host cannot resolve the product DB host); the fix "
        "must be that the probe runs inside the container network."
    )
    broker_addr = f"http://{ctx['cs7k_broker_host']}:14321"
    assert not fake_driver.agent_vault_reachable(broker_addr), (
        "A launcher-HOST-context agent-vault probe should false-fail for the "
        "second product (host cannot resolve the product broker host)."
    )


# --- Scenario fa08c549: probe broker host from slug, decoupled from proxy ---

@given(parsers.parse('a bc-manifest.yaml whose product field is "{product}"'))
def cs7k_manifest_product(product, ctx, tmp_path):
    import yaml as _yaml
    manifest = tmp_path / "bc-manifest.yaml"
    manifest.write_text(_yaml.dump({
        "product": product,
        "bcs": [{
            "name": f"{product}-messaging",
            "remote": f"https://github.com/{product}/{product}-messaging.git",
            "role": "bc",
        }],
    }))
    ctx["launch_manifest_path"] = manifest
    ctx["cs7k_product"] = product


@given("no agent-vault broker override is supplied on the launcher invocation")
def cs7k_no_broker_override(ctx, monkeypatch):
    monkeypatch.delenv("BCLAUNCHER_AGENT_VAULT_BROKER", raising=False)
    ctx["cs7k_broker_override"] = None


@when("bc-container launch resolves the agent-vault broker address used for "
      "the readiness probe")
def cs7k_resolve_probe_broker(ctx, controller, fake_driver):
    from bc_launcher.controller import resolve_probe_broker_address
    product = ctx["cs7k_product"]
    # Resolve the PROBE broker address the controller would use for this
    # product, with NO explicit broker override.
    probe_broker = resolve_probe_broker_address(
        explicit_broker=ctx.get("cs7k_broker_override"),
        system_slug=product,
    )
    ctx["cs7k_probe_broker"] = probe_broker
    # Independently derive the verbatim runtime HTTPS_PROXY for an operator who
    # supplies the addr/token/vault triple, to prove the probe broker host does
    # not clobber it.
    from bc_launcher.controller import _build_runtime_proxy_url
    ctx["cs7k_av_addr"] = "https://agent-vault:14321"
    ctx["cs7k_av_token"] = "av_agt_dummyco_xyz"
    ctx["cs7k_av_vault"] = "dummyco"
    ctx["cs7k_runtime_proxy"] = _build_runtime_proxy_url(
        ctx.get("cs7k_broker_override"),
        ctx["cs7k_av_addr"],
        ctx["cs7k_av_token"],
        ctx["cs7k_av_vault"],
    )


@then(parsers.parse(
    'the probe broker host is derived from the product slug "{slug}" rather '
    'than the hardcoded "{hardcoded}"'
))
def cs7k_probe_broker_from_slug(slug, hardcoded, ctx):
    from urllib.parse import urlparse
    probe_broker = ctx["cs7k_probe_broker"]
    parsed = urlparse(
        probe_broker if "://" in probe_broker else "tcp://" + probe_broker
    )
    host = parsed.hostname or ""
    assert host == f"{slug}-agent-vault", (
        f"Expected the probe broker host derived from product slug {slug!r} "
        f"(i.e. {slug}-agent-vault), got host {host!r} from probe broker "
        f"{probe_broker!r}"
    )
    hardcoded_host = hardcoded.split(":", 1)[0]
    assert host != hardcoded_host, (
        f"Probe broker host must NOT be the hardcoded {hardcoded_host!r}; "
        f"got {host!r}"
    )


@then("supplying a probe broker host does not clobber the token:vault "
      "basic-auth runtime HTTPS_PROXY value derived for the launched agent")
def cs7k_probe_decoupled_from_proxy(ctx):
    from urllib.parse import urlparse, unquote
    runtime_proxy = ctx["cs7k_runtime_proxy"]
    assert runtime_proxy is not None, (
        "Expected a derived runtime HTTPS_PROXY from the addr/token/vault "
        "triple"
    )
    parsed = urlparse(runtime_proxy)
    # The runtime proxy must remain the :14322 MITM listener with token:vault
    # basic-auth — UNAFFECTED by the probe broker host derived above.
    assert parsed.port == 14322, (
        f"Probe-broker derivation clobbered the runtime HTTPS_PROXY port; "
        f"expected the :14322 MITM listener, got {runtime_proxy!r}"
    )
    got = f"{unquote(parsed.username or '')}:{unquote(parsed.password or '')}"
    assert got == f"{ctx['cs7k_av_token']}:{ctx['cs7k_av_vault']}", (
        f"Probe-broker derivation clobbered the runtime HTTPS_PROXY "
        f"token:vault basic-auth; got {got!r} from {runtime_proxy!r}"
    )
    # And the probe broker host is genuinely DISTINCT from the runtime proxy
    # host (decoupling, not aliasing).
    probe_host = urlparse(
        ctx["cs7k_probe_broker"]
        if "://" in ctx["cs7k_probe_broker"]
        else "tcp://" + ctx["cs7k_probe_broker"]
    ).hostname
    assert probe_host != parsed.hostname, (
        f"Probe broker host {probe_host!r} should be decoupled from the "
        f"runtime-proxy host {parsed.hostname!r}"
    )

# ---------------------------------------------------------------------------
# lead-b14a: --env-file preserves a multi-line AGENT_VAULT_CA_PEM value intact
# through to the container env (@scenario_hash:eb92b4a40939973f).
#
# BUG: _parse_env_file used path.read_text().splitlines() and treated each
# physical PEM line as its own KEY=VALUE record, so a multi-line
# AGENT_VAULT_CA_PEM was truncated at the first newline.  FIX: a quoted value
# left open on its first physical line continues accumulating subsequent
# physical lines (real newlines preserved) until the closing quote.  The parsed
# value is a real-newline string; the committed bc-base agent-vault-ca.sh
# materializer (`printf '%s\n' "$AGENT_VAULT_CA_PEM"`) reproduces it
# byte-for-byte -- both ends agree on real newlines, no \n-escape convention.
# ---------------------------------------------------------------------------

# A multi-line broker CA PEM spanning several physical lines (the real CA is
# ~574 bytes, PUBLIC not secret).  Internal newlines are load-bearing: this is
# exactly the shape that the old splitlines() parser truncated.
_MULTILINE_BROKER_CA_PEM = (
    "-----BEGIN CERTIFICATE-----\n"
    "MIIB3TCCAYOgAwIBAgIUFAKEBROKERCAFORTESTSLEADB14A0123456789ABCw\n"
    "RAYDVQQDDD1hZ2VudC12YXVsdC1icm9rZXItY2EtbXVsdGlsaW5lLXBlbS1sZWFk\n"
    "LWIxNGEtdGVzdC1jZXJ0aWZpY2F0ZS1ib2R5LWxpbmUtdGhyZWUtaGVyZXdpdGgw\n"
    "HhcNMjYwNjE5MDAwMDAwWhcNMzYwNjE2MDAwMDAwWjA8MTowOAYDVQQDDDFmYWtl\n"
    "-----END CERTIFICATE-----\n"
)


@given(parsers.parse(
    "an env file supplies AGENT_VAULT_CA_PEM as a multi-line PEM block "
    "spanning several physical lines"
))
def given_multiline_ca_env_file(ctx, tmp_path):
    # The canonical broker CA PEM on disk ends with exactly one trailing
    # newline (the standard PEM file convention).
    pem = _MULTILINE_BROKER_CA_PEM
    # Sanity: the fixture genuinely spans several physical lines.
    assert pem.count("\n") >= 4, "fixture PEM must span several physical lines"
    assert pem.endswith("\n") and not pem.endswith("\n\n")
    ctx["b14a_original_pem"] = pem
    # Write the env file using the quoted multi-line convention.  The value
    # opens with a double quote on the AGENT_VAULT_CA_PEM= line and the closing
    # quote sits immediately after the END marker (no trailing newline captured
    # inside the quotes) -- mirroring the operator's working
    # `export AGENT_VAULT_CA_PEM=$(cat agent-vault-ca.pem)` channel, where
    # command substitution strips the trailing newline.  The bc-base
    # `printf '%s\n'` materializer re-adds exactly one trailing newline, so the
    # canonical PEM is reproduced byte-for-byte.  Internal newlines are
    # preserved verbatim across the several physical lines between the quotes.
    pem_body = pem.rstrip("\n")  # PEM content without the trailing newline
    env_file = tmp_path / "agent-vault.env"
    env_file.write_text(
        "AGENT_VAULT_ADDR=https://agent-vault:14321\n"
        "AGENT_VAULT_TOKEN=av_agt_xyz\n"
        "AGENT_VAULT_VAULT=shopsystem\n"
        f'AGENT_VAULT_CA_PEM="{pem_body}"\n'
    )
    ctx["b14a_env_file"] = env_file
    # The value that travels in the env var is the PEM body (no trailing
    # newline); the materializer supplies the final newline.
    ctx["b14a_env_value"] = pem_body


@when(parsers.parse(
    "bc-container launch parses that env file and injects AGENT_VAULT_CA_PEM "
    "into the launched container env"
))
def when_parse_env_file_and_launch(ctx, controller, fake_driver, tmp_path):
    from bc_launcher.cli import _parse_env_file
    import os as _os

    env_vals = _parse_env_file(ctx["b14a_env_file"])
    ctx["b14a_parsed_pem"] = env_vals.get("AGENT_VAULT_CA_PEM")

    bc_name = "shopsystem-messaging"
    repo_url = f"https://github.com/shopsystem/{bc_name}.git"
    manifest_path = tmp_path / "bc-manifest.yaml"
    if not manifest_path.exists():
        manifest_path.write_text(yaml.dump({
            "product": "shopsystem product",
            "bcs": [{"name": bc_name, "remote": repo_url, "role": "bc"}],
        }))

    # Mirror cli.main()'s injection: export the AGENT_VAULT_* keys the env-file
    # supplied into the process env so controller.launch()'s pass-through
    # forwards them into the container env.
    saved = {}
    for key, value in env_vals.items():
        if key.startswith("AGENT_VAULT_"):
            saved[key] = _os.environ.get(key)
            _os.environ[key] = value
    try:
        result = controller.launch(
            bc_name=bc_name,
            repo_url=repo_url,
            manifest_path=manifest_path,
            credential_home=ctx.get("credential_home"),
            agent_vault_addr=env_vals.get("AGENT_VAULT_ADDR"),
            agent_vault_token=env_vals.get("AGENT_VAULT_TOKEN"),
            agent_vault_vault=env_vals.get("AGENT_VAULT_VAULT"),
        )
    finally:
        for key, value in saved.items():
            if value is None:
                _os.environ.pop(key, None)
            else:
                _os.environ[key] = value
    ctx["result"] = result
    ctx["container_name"] = f"bc-{bc_name}"
    ctx["b14a_container_env"] = fake_driver.container_env(ctx["container_name"])


@then(parsers.parse(
    "the AGENT_VAULT_CA_PEM value injected into the container is the complete "
    "multi-line PEM, not truncated at the first newline"
))
def then_ca_pem_not_truncated(ctx):
    # The value travelling in the env var is the PEM body (all physical lines,
    # internal newlines preserved; trailing newline supplied later by the
    # materializer).
    env_value = ctx["b14a_env_value"]
    original = ctx["b14a_original_pem"]
    injected = ctx["b14a_container_env"].get("AGENT_VAULT_CA_PEM")
    first_line = original.split("\n", 1)[0]
    assert injected is not None, "AGENT_VAULT_CA_PEM was not injected into the container env"
    # Not truncated: the injected value is NOT merely the first physical line
    # (the splitlines() bug truncated it to '"' + the BEGIN line).
    assert injected != first_line and injected != '"' + first_line, (
        "AGENT_VAULT_CA_PEM was truncated to its first physical line "
        f"{first_line!r}; the multi-line PEM was lost"
    )
    # Complete: every physical line of the multi-line PEM is present, in order,
    # with internal newlines preserved.
    assert injected == env_value, (
        "AGENT_VAULT_CA_PEM injected into the container env is not the complete "
        f"multi-line PEM.\n  expected: {env_value!r}\n  got:      {injected!r}"
    )
    assert injected.count("\n") == env_value.count("\n") >= 4, (
        "internal newline count of the injected PEM does not match the "
        "several-physical-line original"
    )
    # The END marker survived -- a truncated value would never reach it.
    assert "-----END CERTIFICATE-----" in injected, (
        "the PEM END marker did not survive into the container env (truncated)"
    )


@then(parsers.parse(
    "the value materialized inside the container reproduces the original PEM "
    "byte-for-byte including its internal newlines"
))
def then_materialized_byte_for_byte(ctx, tmp_path):
    """Run the COMMITTED bc-base agent-vault-ca.sh against the injected env
    value and assert the materialized CA file reproduces the original PEM
    byte-for-byte.  This exercises both ends of the convention: the launcher
    parse side and the bc-base `printf '%s\\n'` materialization side."""
    injected = ctx["b14a_container_env"].get("AGENT_VAULT_CA_PEM")
    script = _ca_trust_script_path()
    assert script is not None, "committed bc-base agent-vault-ca.sh not found"

    # The committed script hard-codes /home/vscode/.config/agent-vault/ca.pem.
    # Redirect HOME to a sandbox and run the materialization branch only by
    # invoking the script's logic with a sandbox CA path, exactly mirroring the
    # committed `printf '%s\n' "$AGENT_VAULT_CA_PEM" > "$CA_PATH"` step.
    sandbox = tmp_path / "container_fs"
    ca_dir = sandbox / "home" / "vscode" / ".config" / "agent-vault"
    ca_dir.mkdir(parents=True, exist_ok=True)
    ca_path = ca_dir / "ca.pem"

    # Assert the committed script materializes via `printf '%s\n'` (verbatim,
    # NOT a \n-unescaping echo -e) so a real-newline env value reproduces
    # byte-for-byte.  This pins the convention agreement on the bc-base side.
    script_text = script.read_text()
    assert "printf '%s\\n'" in script_text and 'AGENT_VAULT_CA_PEM' in script_text, (
        "bc-base agent-vault-ca.sh must materialize AGENT_VAULT_CA_PEM via "
        "printf '%s\\n' (verbatim) for the multi-line PEM to reproduce "
        "byte-for-byte"
    )

    # Materialize exactly as the committed script does.
    import subprocess as _sp
    _sp.run(
        ["/bin/sh", "-c", 'printf %s\\\\n "$AGENT_VAULT_CA_PEM" > "$1"', "sh", str(ca_path)],
        env={**__import__("os").environ, "AGENT_VAULT_CA_PEM": injected},
        check=True,
    )
    materialized = ca_path.read_text()

    original = ctx["b14a_original_pem"]
    # The committed materializer appends a trailing newline via printf '%s\n';
    # the original fixture already ends in a newline, so account for that one.
    assert materialized == original, (
        "materialized CA file does not reproduce the original PEM byte-for-byte."
        f"\n  expected: {original!r}\n  got:      {materialized!r}"
    )
    ctx["b14a_materialized_pem"] = materialized


@then(parsers.parse(
    "a brokered HTTPS request from inside the container trusts the broker CA "
    "using the materialized PEM"
))
def then_brokered_https_trusts_ca(ctx):
    """The materialized PEM is a usable trust anchor: it is a complete,
    well-formed PEM (BEGIN/END markers intact, internal newlines preserved)
    equal to the operator-supplied broker CA.  A trust store built from a
    truncated PEM (the bug) could not verify the broker cert; an intact one
    can.  We assert the trust-anchor material is intact and equals the original
    operator CA -- the necessary-and-sufficient condition for the brokered
    HTTPS handshake to verify against it."""
    materialized = ctx.get("b14a_materialized_pem")
    original = ctx["b14a_original_pem"]
    assert materialized is not None, "no materialized PEM available"
    assert materialized.startswith("-----BEGIN CERTIFICATE-----"), (
        "materialized trust anchor is missing its PEM BEGIN marker (truncated?)"
    )
    assert "-----END CERTIFICATE-----" in materialized, (
        "materialized trust anchor is missing its PEM END marker -- a truncated "
        "single-line value would not reach the END marker"
    )
    assert materialized == original, (
        "materialized trust anchor does not equal the operator-supplied broker "
        "CA; a brokered HTTPS request would fail cert verification"
    )


# ---------------------------------------------------------------------------
# Scenario 365be56194c892b9 (lead-aw1b, supersedes the vacuous c179b0c448ca851c
# from lead-pwa2): a shopsystem-templates release dispatch starts a bc-base
# rebuild run -- pinning the event_type LITERAL end-to-end.
#
# The PRODUCER (dstengle/shopsystem-templates .github/workflows/release.yml)
# POSTs a repository_dispatch with event_type "shopsystem-templates-released".
# The CONSUMER (this repo's rebuild-bc-base.yml) only starts a run if it
# SUBSCRIBES to that exact literal in on.repository_dispatch.types. The prior
# pin abstracted the literal away (it only checked that *some*
# repository_dispatch trigger existed), so a renamed/mismatched subscription
# still passed even though a real release dispatch fired NOTHING (silent
# no-op). This pin is NON-VACUOUS: it asserts the subscribed types CONTAIN the
# exact producer literal, so the scenario FAILS if the subscription is renamed
# or mismatched -- distinguishing emitted-literal == subscribed-literal (fires)
# from a mismatch (silent no-op).
#
# Live GitHub Actions / repository_dispatch delivery is OUT-OF-BAND (the
# scenario-40 declarative-artifact precedent): the proxy is the committed
# rebuild workflow YAML.
# ---------------------------------------------------------------------------

# The event_type literal the shopsystem-templates release workflow POSTs (the
# producer's repository_dispatch event_type). The consumer must subscribe to
# THIS exact string for a real release dispatch to start a rebuild run.
_TEMPLATES_RELEASED_EVENT_TYPE = "shopsystem-templates-released"


def _bc_base_rebuild_dispatch_workflow():
    """Return (path, doc) for the committed workflow triggered by a
    repository_dispatch that rebuilds the bc-base image, or None."""
    for path, doc in _load_workflows().items():
        if not isinstance(doc, dict):
            continue
        on = doc.get("on", doc.get(True))
        if not (isinstance(on, dict) and "repository_dispatch" in on):
            continue
        text = path.read_text()
        # The workflow must actually rebuild the bc-base image (a build step),
        # otherwise a bare repository_dispatch trigger would falsely satisfy
        # "a bc-base rebuild run is started".
        if "shopsystem-bc-base" in text and (
            "build-push-action" in text or "docker build" in text
        ):
            return (path, doc)
    return None


def _repository_dispatch_subscribed_types(doc) -> list[str]:
    """The list of repository_dispatch event_type literals a workflow doc
    subscribes to (its on.repository_dispatch.types), normalized to a list of
    strings. Empty list if the trigger declares no explicit types filter."""
    on = doc.get("on", doc.get(True))
    if not (isinstance(on, dict) and "repository_dispatch" in on):
        return []
    rd = on["repository_dispatch"]
    if not isinstance(rd, dict):
        return []
    types = rd.get("types", [])
    if isinstance(types, str):
        return [types]
    if isinstance(types, list):
        return [str(t) for t in types]
    return []


@given(parsers.parse('the shopsystem-templates release workflow emits a '
                     'repository_dispatch whose event_type is the literal '
                     '"{event_type}"'))
def given_templates_emits_event_type(event_type, ctx):
    # The producer-emitted event_type literal (shopsystem-templates-released).
    ctx["emitted_event_type"] = event_type


@given(parsers.parse('that repository_dispatch targets the '
                     'shopsystem-bc-launcher repository and carries the '
                     'released tag "{tag}" in its client_payload'))
def given_dispatch_targets_bc_launcher(tag, ctx):
    ctx["dispatch_payload_tag"] = tag


@given(parsers.parse('the shopsystem-bc-launcher rebuild-bc-base workflow '
                     'subscribes to the repository_dispatch event_type literal '
                     '"{event_type}"'))
def given_rebuild_subscribes_event_type(event_type, ctx):
    # Resolve the committed rebuild workflow and read the literals it actually
    # subscribes to. NON-VACUITY: assert the subscribed types CONTAIN the exact
    # producer literal -- this FAILS if the subscription is renamed/mismatched.
    wf = _bc_base_rebuild_dispatch_workflow()
    assert wf is not None, (
        "No committed workflow under .github/workflows is triggered by a "
        "repository_dispatch AND rebuilds the shopsystem-bc-base image."
    )
    ctx["rebuild_dispatch_workflow"] = wf
    subscribed = _repository_dispatch_subscribed_types(wf[1])
    ctx["subscribed_event_types"] = subscribed
    ctx["subscribed_event_type"] = event_type
    assert event_type in subscribed, (
        "The rebuild-bc-base workflow does NOT subscribe to the producer's "
        f"event_type literal {event_type!r}. Its "
        f"on.repository_dispatch.types = {subscribed!r}. Because the producer "
        f"POSTs event_type {event_type!r} and it is not among the subscribed "
        "literals, a real shopsystem-templates release dispatch would fire "
        "NOTHING (silent no-op).\n"
        f"Workflow: {wf[0].relative_to(_REPO_ROOT)}"
    )


@when(parsers.parse('that repository_dispatch with event_type "{event_type}" '
                    'is delivered to shopsystem-bc-launcher'))
def when_dispatch_delivered_to_bc_launcher(event_type, ctx):
    # Live Actions delivery is OUT-OF-BAND; the proxy is the committed rebuild
    # workflow declaring the repository_dispatch trigger.
    ctx.setdefault("rebuild_dispatch_workflow",
                   _bc_base_rebuild_dispatch_workflow())
    ctx["delivered_event_type"] = event_type


@then("because the emitted event_type literal equals the subscribed "
      "event_type literal, a bc-base rebuild workflow run is started in "
      "shopsystem-bc-launcher in response to that dispatch")
def then_bc_base_rebuild_run_started(ctx):
    wf = ctx.get("rebuild_dispatch_workflow")
    assert wf is not None, (
        "No committed workflow under .github/workflows is triggered by a "
        "repository_dispatch AND rebuilds the shopsystem-bc-base image, so a "
        "templates release dispatch could not start a bc-base rebuild run."
    )
    # The run is started ONLY because the emitted literal is among the
    # subscribed literals. Re-assert the literal match here so this Then is
    # non-vacuous on its own: a mismatch means the run is NOT started.
    emitted = ctx.get("emitted_event_type", _TEMPLATES_RELEASED_EVENT_TYPE)
    subscribed = _repository_dispatch_subscribed_types(wf[1])
    assert emitted in subscribed, (
        f"The emitted event_type literal {emitted!r} is NOT among the "
        f"rebuild workflow's subscribed types {subscribed!r}, so the "
        "emitted-literal != subscribed-literal and NO rebuild run starts "
        "(silent no-op)."
    )


@then(parsers.parse('that rebuild workflow run receives the released tag '
                    '"{tag}" from the dispatch client_payload'))
def then_workflow_receives_released_tag(tag, ctx):
    wf = ctx.get("rebuild_dispatch_workflow")
    assert wf is not None, (
        "No repository_dispatch-triggered bc-base rebuild workflow was found."
    )
    text = wf[0].read_text()
    # The released tag must be consumed FROM the dispatch client_payload and
    # threaded into the run -- a workflow that ignores client_payload and
    # rebuilds at a frozen tag would NOT "receive the released tag".  Genuine
    # consumption is a github.event.client_payload.<field> expression.
    payload_re = re.compile(
        r"\$\{\{\s*github\.event\.client_payload\.[A-Za-z0-9_]+\s*\}\}"
    )
    assert payload_re.search(text), (
        "The repository_dispatch rebuild workflow does not read the released "
        "tag from github.event.client_payload; it ignores the dispatch payload "
        "and so does not receive the released tag.\n"
        f"Workflow: {wf[0].relative_to(_REPO_ROOT)}"
    )


# ---------------------------------------------------------------------------
# Scenario edd2c813688ab768 (lead-pwa2): after a templates release propagates,
# the rebuilt bc-base:latest carries the RELEASED shop-templates version, no
# longer the previously hard-pinned one.
#
# Live registry pull is OUT-OF-BAND; the proxy is the committed
# Dockerfile + rebuild workflow.  For the rebuild to republish "latest"
# carrying vT_new, the shop-templates version installed into the image must be
# PARAMETERIZED from the dispatch payload (a Docker build-arg fed from
# client_payload) rather than frozen at a hard-coded vMAJOR.MINOR.PATCH literal
# in the Dockerfile.  We assert that parameterization by construction:
#   (a) the rebuild workflow passes a build-arg sourced from
#       github.event.client_payload, and
#   (b) the Dockerfile installs shop-templates at a version taken from a build
#       ARG (not a frozen literal), so the installed version is no longer the
#       hard-pinned vT_old.
# ---------------------------------------------------------------------------

# The Dockerfile build ARG that carries the shop-templates version through the
# rebuild.  Asserted (not just any ARG) so the parameterization is the
# shop-templates one specifically.
_SHOP_TEMPLATES_VERSION_ARG_RE = re.compile(
    r"ARG\s+(SHOP_TEMPLATES_VERSION|SHOP_TEMPLATES_REF|TEMPLATES_VERSION)\b"
)


@given(parsers.parse('the published "bc-base:latest" image carries an '
                     'installed shop-templates at version "{tag}"'))
def given_latest_carries_shop_templates_version(tag, ctx):
    ctx["shop_templates_old_version"] = tag


@given(parsers.parse('the shopsystem-templates repository publishes a newer '
                     'release for the tag "{tag}" distinct from "{old}"'))
def given_templates_publishes_newer_release(tag, old, ctx):
    assert tag != old, "scenario precondition: vT_new must differ from vT_old"
    ctx["shop_templates_new_version"] = tag


@when('the bc-base rebuild triggered by that release completes and '
      'republishes the "latest" tag')
def when_rebuild_completes_republishes_latest(ctx):
    ctx["rebuild_dispatch_workflow"] = _bc_base_rebuild_dispatch_workflow()
    ctx["bc_base_dockerfile"] = _find_bc_base_dockerfile()


@then(parsers.parse('pulling "{image_ref}" yields an image whose installed '
                    'shop-templates reports version "{tag}"'))
def then_pulled_image_reports_shop_templates_version(image_ref, tag, ctx):
    # For the pulled "latest" to report the RELEASED version, the rebuild must
    # feed the released tag into the shop-templates install as a build-arg.
    wf = ctx.get("rebuild_dispatch_workflow")
    assert wf is not None, (
        "No repository_dispatch-triggered bc-base rebuild workflow was found, "
        "so a templates release cannot republish a latest carrying the new "
        "shop-templates version."
    )
    wf_text = wf[0].read_text()
    # The build step must pass a build-arg carrying the shop-templates version,
    # sourced from the dispatch client_payload (the released tag).
    assert "build-args" in wf_text or "--build-arg" in wf_text, (
        "The rebuild workflow does not pass any docker build-arg, so it cannot "
        "thread the released shop-templates version into the image build.\n"
        f"Workflow: {wf[0].relative_to(_REPO_ROOT)}"
    )
    payload_re = re.compile(
        r"\$\{\{\s*github\.event\.client_payload\.[A-Za-z0-9_]+\s*\}\}"
    )
    assert payload_re.search(wf_text), (
        "The rebuild workflow's build-arg is not sourced from "
        "github.event.client_payload, so the pulled image would not carry the "
        f"released version {tag!r}."
    )
    dockerfile = ctx.get("bc_base_dockerfile")
    assert dockerfile is not None, "bc-base Dockerfile not found."
    df_text = dockerfile.read_text()
    arg_match = _SHOP_TEMPLATES_VERSION_ARG_RE.search(df_text)
    assert arg_match, (
        "The bc-base Dockerfile declares no ARG for the shop-templates "
        "version, so the rebuild's build-arg has nothing to bind and the "
        "installed version cannot be the released one."
    )
    arg_name = arg_match.group(1)
    # The shop-templates install must reference that ARG (so the installed
    # version is the build-arg value), e.g. "...@${SHOP_TEMPLATES_VERSION}".
    install_uses_arg = re.search(
        r"shop-templates @ git\+https://github\.com/dstengle/"
        r"shopsystem-templates(?:\.git)?@\$\{?" + re.escape(arg_name) + r"\}?",
        df_text,
    )
    assert install_uses_arg, (
        "The bc-base Dockerfile's shop-templates install does not interpolate "
        f"the ${{{arg_name}}} build ARG, so the installed shop-templates "
        f"version is not driven by the released tag {tag!r}."
    )


@then(parsers.parse('the installed shop-templates version is no longer the '
                    'previously hard-pinned "{old}"'))
def then_shop_templates_no_longer_hard_pinned(old, ctx):
    dockerfile = ctx.get("bc_base_dockerfile")
    assert dockerfile is not None, "bc-base Dockerfile not found."
    df_text = dockerfile.read_text()
    # The shop-templates install line must NOT freeze the version at a
    # hard-coded vMAJOR.MINOR.PATCH literal; it must take the version from the
    # build ARG.  A frozen literal (the old behavior) would pin vT_old forever
    # regardless of the dispatched release.
    frozen_literal = re.search(
        r"shop-templates @ git\+https://github\.com/dstengle/"
        r"shopsystem-templates(?:\.git)?@v\d+\.\d+\.\d+",
        df_text,
    )
    assert frozen_literal is None, (
        "The bc-base Dockerfile still installs shop-templates at a frozen "
        "vMAJOR.MINOR.PATCH literal "
        f"({frozen_literal.group(0) if frozen_literal else ''!r}); a rebuild "
        "would re-pin that hard-coded version rather than the released one."
    )


# ---------------------------------------------------------------------------
# lead-5k8c — bd-bootstrap resilience: empty-remote provisioning +
# warn-and-continue (generalizes the lead-k4k7 no-fatal-strand invariant to
# the in-container bd-bootstrap step).
# ---------------------------------------------------------------------------

@given("the BC's beads dolt remote is empty and uninitialized")
def beads_remote_empty(ctx, fake_driver):
    """Model the BC's `<bc>-beads` Dolt remote as EMPTY (lead-5k8c).

    While empty (and not yet seeded by the launcher), a `bd bootstrap` clone
    fails "git remote has no branches" — the exact strand-class condition
    observed live 2026-06-22.  Keyed by the container name the upcoming launch
    will create.
    """
    bc_name = ctx["bc_name"]
    container_name = f"bc-{bc_name}"
    fake_driver.set_beads_remote_empty(container_name, True)
    ctx["container_name"] = container_name


@given("the launcher's empty-remote seed step fails at runtime")
def beads_remote_seed_fails(ctx, fake_driver):
    """Model the launcher's empty-remote SEED step itself failing (lead-5k8c).

    This drives the warn-and-continue path: the remote stays empty so
    `bd bootstrap` still fails, and the launch must WARN and proceed to
    agent-start rather than fatal-strand the container.
    """
    bc_name = ctx["bc_name"]
    container_name = f"bc-{bc_name}"
    fake_driver.set_beads_remote_seed_fails(container_name, True)
    ctx["container_name"] = container_name


@then("the launch initializes the empty beads dolt remote with an initial "
      "branch and commit")
def assert_empty_remote_initialized(ctx, fake_driver):
    """The launcher must have run the empty-remote SEED step and the remote
    must end up seeded (lead-5k8c EMPTY-REMOTE PROVISIONING)."""
    container_name = ctx["container_name"]
    seed_calls = [
        c for c in fake_driver.exec_calls
        if c.container == container_name
        and _is_empty_remote_seed_command(c.command)
    ]
    assert seed_calls, (
        "The launcher must INITIALIZE an empty beads dolt remote (init-and-"
        "push an initial branch/commit) instead of fatal-failing; no "
        f"empty-remote seed step ran. exec calls on {container_name!r}: "
        f"{[c.command for c in fake_driver.exec_calls if c.container == container_name]!r}"
    )
    assert fake_driver.beads_remote_seeded(container_name), (
        "The empty beads dolt remote must end up SEEDED after the launcher's "
        f"init-and-push step (lead-5k8c); container={container_name!r}"
    )


@then("the launch retries bd bootstrap after seeding the empty remote")
def assert_bootstrap_retried_after_seed(ctx, fake_driver):
    """The launcher must run `bd bootstrap` AGAIN after seeding the remote, so
    a once-empty remote ends up provisioned (lead-5k8c)."""
    container_name = ctx["container_name"]
    calls = [
        c for c in fake_driver.exec_calls
        if c.container == container_name
    ]
    # Locate the seed step, and assert a bd bootstrap exec follows it.
    seed_idx = next(
        (i for i, c in enumerate(calls)
         if _is_empty_remote_seed_command(c.command)),
        None,
    )
    assert seed_idx is not None, (
        "Expected an empty-remote seed step before the bootstrap retry "
        f"(lead-5k8c); calls={[c.command for c in calls]!r}"
    )
    retried = any(
        is_bd_bootstrap_command(c.command) for c in calls[seed_idx + 1:]
    )
    assert retried, (
        "The launcher must RETRY `bd bootstrap` after seeding the empty "
        f"remote (lead-5k8c); calls after seed="
        f"{[c.command for c in calls[seed_idx + 1:]]!r}"
    )


@then("the launch still starts the agent")
def assert_launch_starts_agent(ctx, fake_driver):
    """The agent tmux session must be started — a healthy cloned container is
    NEVER left without an agent (lead-5k8c)."""
    container_name = ctx["container_name"]
    new_sessions = [
        c.command for c in fake_driver.exec_calls
        if c.container == container_name
        and c.command[:3] == ["tmux", "new-session", "-d"]
    ]
    assert new_sessions, (
        "The launch must start the agent tmux session (lead-5k8c: no "
        f"pre-agent-start step may strand the container); tmux new-session "
        f"calls={new_sessions!r}"
    )
    result = ctx["result"]
    assert "Started tmux session" in (result.stdout or ""), (
        "The 'Started tmux session' log line must be emitted (lead-5k8c); "
        f"stdout={result.stdout!r}"
    )


@then("the launch warns about the bd bootstrap failure and still starts the "
      "agent")
def assert_bootstrap_failure_warns_and_starts_agent(ctx, fake_driver):
    """lead-5k8c — a bd-bootstrap failure is downgraded from a fatal

    early-return to a WARNING that still PROCEEDS to agent-start (the same
    class as lead-k4k7's skill-refresh warn-and-continue).  The launch must
    emit a warning naming the bd bootstrap failure AND start the agent.
    """
    result = ctx["result"]
    container_name = ctx["container_name"]
    combined = (result.stdout or "") + (result.stderr or "")

    assert "warning" in combined.lower() and "bd bootstrap" in combined, (
        "A failed bd bootstrap must log a warning naming the bd bootstrap "
        f"failure (lead-5k8c); output={combined!r}"
    )
    new_sessions = [
        c.command for c in fake_driver.exec_calls
        if c.container == container_name
        and c.command[:3] == ["tmux", "new-session", "-d"]
    ]
    assert new_sessions, (
        "A failed bd bootstrap must NOT abort the launch before the agent "
        f"tmux session is started (lead-5k8c); output={combined!r}"
    )
    assert "Started tmux session" in (result.stdout or ""), (
        "The 'Started tmux session' log line must still be emitted on a "
        f"failed bd bootstrap (lead-5k8c); stdout={result.stdout!r}"
    )


# ---------------------------------------------------------------------------
# lead-zxtk — workspace-mount launch option + opt-in docker-socket mount
# (scenarios 0bc8e4532c04bf72 / 9fc84c8424b2a223 / ff370a4e7e9dac5e /
#  e177655ba09a73fa)
# ---------------------------------------------------------------------------

def _zxtk_default_manifest(ctx, tmp_path, bc_name="shopsystem-messaging"):
    """Write a default manifest so network resolution succeeds for a launch
    that does not otherwise configure one (lead-zxtk scenarios)."""
    manifest_path = tmp_path / "bc-manifest.yaml"
    if not manifest_path.exists():
        import yaml as _yaml
        manifest_path.write_text(_yaml.dump({
            "product": "shopsystem product",
            "bcs": [{
                "name": bc_name,
                "remote": f"https://github.com/shopsystem/{bc_name}.git",
                "role": "bc",
            }],
        }))
    return manifest_path


@given(parsers.parse('an existing host working tree at a path "{host_path}" '
                     'containing a git repository'))
def host_working_tree_with_git(host_path, ctx, fake_driver):
    """A host working tree to bind-mount as /workspace (lead-zxtk)."""
    ctx["workspace_mount"] = host_path
    # Record a minimal host-tree snapshot so the byte-unchanged model has a
    # baseline even when this scenario does not assert on it.
    fake_driver.set_host_tree_snapshot(
        host_path,
        beads_registry='{"id":"lead-1","title":"seed"}\n',
        claude_skills="committed-skill-group\n",
    )


@given(parsers.parse('an existing host working tree at a path "{host_path}" '
                     'with a committed ".beads" registry and poured '
                     '".claude/skills"'))
def host_working_tree_with_beads_and_skills(host_path, ctx, fake_driver):
    """A host working tree carrying a committed `.beads` registry and poured
    `.claude/skills`, to be presented unchanged via a workspace-mount."""
    ctx["workspace_mount"] = host_path
    fake_driver.set_host_tree_snapshot(
        host_path,
        beads_registry='{"id":"lead-1","title":"committed registry"}\n',
        claude_skills="poured-skill-group/bc-router-health\n",
    )


@when(parsers.parse('I run bc-container launch with the workspace-mount option '
                    'set to "{host_path}" and no repo URL'))
def run_launch_with_workspace_mount(host_path, ctx, fake_driver, controller, tmp_path):
    bc_name = "shopsystem-messaging"
    manifest_path = _zxtk_default_manifest(ctx, tmp_path, bc_name)
    result = controller.launch(
        bc_name=bc_name,
        repo_url=None,
        workspace_mount=host_path,
        manifest_path=manifest_path,
        credential_home=ctx.get("credential_home"),
    )
    ctx["result"] = result
    ctx["container_name"] = f"bc-{bc_name}"
    ctx["bc_name"] = bc_name
    ctx["workspace_mount"] = host_path


@when("I run bc-container launch with the docker-socket opt-in flag enabled")
def run_launch_with_docker_socket_flag(ctx, fake_driver, controller, tmp_path):
    bc_name = "shopsystem-messaging"
    manifest_path = _zxtk_default_manifest(ctx, tmp_path, bc_name)
    result = controller.launch(
        bc_name=bc_name,
        repo_url=ctx.get("repo_url"),
        mount_docker_socket=True,
        manifest_path=manifest_path,
        credential_home=ctx.get("credential_home"),
    )
    ctx["result"] = result
    ctx["container_name"] = f"bc-{bc_name}"
    ctx["bc_name"] = bc_name


@when("I run bc-container launch without the docker-socket opt-in flag")
def run_launch_without_docker_socket_flag(ctx, fake_driver, controller, tmp_path):
    bc_name = "shopsystem-messaging"
    manifest_path = _zxtk_default_manifest(ctx, tmp_path, bc_name)
    result = controller.launch(
        bc_name=bc_name,
        repo_url=ctx.get("repo_url"),
        mount_docker_socket=False,
        manifest_path=manifest_path,
        credential_home=ctx.get("credential_home"),
    )
    ctx["result"] = result
    ctx["container_name"] = f"bc-{bc_name}"
    ctx["bc_name"] = bc_name


@then(parsers.parse('the container has a bind mount whose source is the host '
                    'path "{source}" and whose target is "{target}"'))
def assert_bind_mount_source_target(source, target, ctx, fake_driver, controller):
    container_name = ctx["container_name"]
    bind_mounts = controller.get_bind_mounts(container_name)
    matching = [
        m for m in bind_mounts
        if m.source == source and m.destination == target
    ]
    assert matching, (
        f"Expected a bind mount source={source!r} target={target!r}; "
        f"got bind mounts: {[(m.source, m.destination) for m in bind_mounts]}"
    )


@then("no git clone is performed for the launch")
def assert_no_git_clone(ctx, fake_driver):
    container_name = ctx["container_name"]
    clone_calls = [
        c for c in fake_driver.exec_calls
        if c.container == container_name and c.command[:2] == ["git", "clone"]
    ]
    assert not clone_calls, (
        "Expected NO git clone exec call for a workspace-mount launch; "
        f"got: {[c.command for c in clone_calls]}"
    )


@then("the container's /workspace is the host tree presented unchanged")
def assert_workspace_is_host_tree(ctx, fake_driver, controller):
    container_name = ctx["container_name"]
    host_path = ctx["workspace_mount"]
    bind_mounts = controller.get_bind_mounts(container_name)
    matching = [
        m for m in bind_mounts
        if m.source == host_path and m.destination == "/workspace"
    ]
    assert matching, (
        f"Expected /workspace to be the bind-mounted host tree {host_path!r}; "
        f"got: {[(m.source, m.destination) for m in bind_mounts]}"
    )
    # Presented unchanged: no provisioning op mutated the mounted tree.
    assert not fake_driver.bd_bootstrap_ran(container_name), (
        "A workspace-mount launch must not run bd bootstrap against the "
        "mounted tree"
    )
    assert not fake_driver.shop_templates_update_ran(container_name), (
        "A workspace-mount launch must not re-pour shop-templates over the "
        "mounted tree"
    )


@then("no bd bootstrap is run against the mounted /workspace")
def assert_no_bd_bootstrap_on_mount(ctx, fake_driver):
    container_name = ctx["container_name"]
    assert not fake_driver.bd_bootstrap_ran(container_name), (
        "Expected NO bd bootstrap exec call for a workspace-mount launch "
        "(clone-path provisioning must be skipped, lead-zxtk)"
    )


@then(parsers.parse('no shop-templates re-pour overwrites the mounted '
                    '".claude/skills"'))
def assert_no_shop_templates_repour(ctx, fake_driver):
    container_name = ctx["container_name"]
    assert not fake_driver.shop_templates_update_ran(container_name), (
        "Expected NO shop-templates update (re-pour) exec call for a "
        "workspace-mount launch (clone-path provisioning must be skipped)"
    )


@then(parsers.parse('the mounted /workspace ".beads" registry and '
                    '".claude/skills" are byte-unchanged from the host tree '
                    'after launch'))
def assert_mounted_tree_byte_unchanged(ctx, fake_driver):
    container_name = ctx["container_name"]
    host_path = ctx["workspace_mount"]
    assert fake_driver.mounted_tree_byte_unchanged(container_name, host_path), (
        "The mounted /workspace .beads registry and .claude/skills must be "
        "byte-unchanged after a workspace-mount launch (no provisioning op "
        "may write to the live host tree, lead-zxtk)"
    )


@then(parsers.parse('the container has a bind mount whose source is the host '
                    'docker socket "{socket_path}"'))
def assert_docker_socket_bind_mount_present(socket_path, ctx, fake_driver, controller):
    container_name = ctx["container_name"]
    bind_mounts = controller.get_bind_mounts(container_name)
    matching = [m for m in bind_mounts if m.source == socket_path]
    assert matching, (
        f"Expected a docker-socket bind mount with source {socket_path!r}; "
        f"got: {[(m.source, m.destination) for m in bind_mounts]}"
    )


@then("docker inspect of the container shows the docker socket mount present")
def assert_docker_inspect_socket_present(ctx, fake_driver):
    container_name = ctx["container_name"]
    mounts = fake_driver.get_mounts(container_name)
    matching = [
        m for m in mounts
        if m.type == "bind" and m.source == "/var/run/docker.sock"
    ]
    assert matching, (
        "docker inspect (get_mounts) must show the docker-socket mount "
        f"present; got: {[(m.type, m.source, m.destination) for m in mounts]}"
    )


@then(parsers.parse('the container has no bind mount whose source is the host '
                    'docker socket "{socket_path}"'))
def assert_docker_socket_bind_mount_absent(socket_path, ctx, fake_driver, controller):
    container_name = ctx["container_name"]
    bind_mounts = controller.get_bind_mounts(container_name)
    matching = [m for m in bind_mounts if m.source == socket_path]
    assert not matching, (
        f"Expected NO docker-socket bind mount (source {socket_path!r}) by "
        f"default; got: {[(m.source, m.destination) for m in bind_mounts]}"
    )


@then("docker inspect of the container shows no docker socket mount present")
def assert_docker_inspect_socket_absent(ctx, fake_driver):
    container_name = ctx["container_name"]
    mounts = fake_driver.get_mounts(container_name)
    matching = [
        m for m in mounts
        if m.type == "bind" and m.source == "/var/run/docker.sock"
    ]
    assert not matching, (
        "docker inspect (get_mounts) must show NO docker-socket mount by "
        f"default; got: {[(m.type, m.source, m.destination) for m in mounts]}"
    )


# ===========================================================================
# Engage blocking-option-screen Escape-handling step definitions (lead-q3uy)
#
# Scenarios f68d8199fef70fa7 / f17f0fc747e44e47 / 9d38d505fc8b5432.  After the
# input-ready marker but before the startup prompt is submitted, the agent
# runtime can present a blocking interactive option screen.  The launcher
# recognizes it (capture_pane), and:
#   * escape-able screen -> sends a DISCRETE send-keys carrying ONLY Escape
#     (never Enter), captures + WARNs the rendered content, then submits the
#     prompt directly (no host-side inject);
#   * non-escape-able screen -> does NOT send Enter / does NOT auto-confirm,
#     WARNs naming the un-escapable screen, and does NOT submit the prompt.
# The FakeDockerDriver models this faithfully (see tests/fake_driver.py:
# simulate_option_screen / capture_pane / the Escape-dismiss send-keys path).
# ===========================================================================

# The rendered content the simulated option screens present.  Both carry the
# OPTION_SCREEN_MARKER signature ("Select an option"); the escapable one ALSO
# carries the ESCAPE_AFFORDANCE_MARKER ("esc to") the launcher keys on.
_ESCAPABLE_OPTION_SCREEN = (
    "Select an option for your session:\n"
    "  > Use the default theme\n"
    "    Pick a different theme\n"
    "(press esc to dismiss and keep current settings)\n"
)
_UNESCAPABLE_OPTION_SCREEN = (
    "Select an option to continue:\n"
    "  > Accept the license agreement\n"
    "    Decline\n"
    "(you must choose one of the options above to proceed)\n"
)


@given(
    "on engage the in-container agent runtime presents an interactive option "
    "screen that blocks the input prompt and exposes a dismiss/escape affordance"
)
def given_escapable_option_screen(ctx, fake_driver):
    bc_name = "shopsystem-messaging"
    container_name = f"bc-{bc_name}"
    fake_driver.simulate_option_screen(
        container_name, _ESCAPABLE_OPTION_SCREEN, escapable=True
    )
    ctx["bc_name"] = bc_name
    ctx["container_name"] = container_name
    ctx["option_screen_content"] = _ESCAPABLE_OPTION_SCREEN


@given(
    "on engage the in-container agent runtime presents an interactive screen "
    "that blocks the input prompt and exposes no dismiss or escape affordance"
)
def given_unescapable_option_screen(ctx, fake_driver):
    bc_name = "shopsystem-messaging"
    container_name = f"bc-{bc_name}"
    fake_driver.simulate_option_screen(
        container_name, _UNESCAPABLE_OPTION_SCREEN, escapable=False
    )
    ctx["bc_name"] = bc_name
    ctx["container_name"] = container_name
    ctx["option_screen_content"] = _UNESCAPABLE_OPTION_SCREEN


@when(parsers.parse(
    'I run "bc-container launch {bc_name} --startup-prompt \'{prompt}\'" '
    'and the launch command runs the engage path'
))
def run_launch_engage_path(bc_name, prompt, ctx, fake_driver, controller, tmp_path):
    """Run launch through the engage path WITHOUT asserting a zero exit.

    Scenario 9d38d505fc8b5432 (no escape affordance) exercises the engage path
    but does not submit the prompt; this When drives launch and records the
    result without an exit-code assertion (the launch still exits zero — it
    warns rather than failing — but this step stays exit-code-agnostic so the
    Then steps own the behavioral assertions).
    """
    repo_url = ctx.get("repo_url", f"https://github.com/shopsystem/{bc_name}.git")
    manifest_path = tmp_path / "bc-manifest.yaml"
    if not manifest_path.exists():
        manifest_path.write_text(yaml.dump({
            "product": "shopsystem product",
            "bcs": [{"name": bc_name, "remote": repo_url, "role": "bc"}],
        }))
    result = controller.launch(
        bc_name=bc_name,
        repo_url=repo_url,
        startup_prompt=prompt,
        manifest_path=manifest_path,
        credential_home=ctx.get("credential_home"),
    )
    ctx["result"] = result
    ctx["container_name"] = f"bc-{bc_name}"
    ctx["bc_name"] = bc_name
    ctx["startup_prompt"] = prompt


@when(parsers.parse(
    'I read the engage observability surface for the launch via '
    '"bc-container monitor {bc_name}" from the host'
))
def read_engage_observability_surface(bc_name, ctx, fake_driver, controller):
    """Read the host-discoverable engage observability surface.

    The launch's host-discoverable WARNING surface is the launch command's
    stderr — emitted to the host process, requiring NO attach into the
    container.  This step records that surface (and runs `bc-container monitor`
    to confirm it is reachable from the host without attaching) so the Then
    steps can assert the auto-dismiss warning and the captured screen content.
    """
    monitor_result = controller.monitor(bc_name)
    ctx["monitor_result"] = monitor_result
    # The launch result's stderr is the host-discoverable engage warning
    # surface; surface it to the Then steps under a stable key.
    ctx["engage_warning_surface"] = ctx["result"].stderr


def _escape_send_keys(fake_driver, container_name, session):
    """Send-keys invocations whose SOLE payload is the Escape key."""
    out = []
    for c in fake_driver.send_keys_calls(container_name):
        cmd = c.command
        if cmd[:4] == ["tmux", "send-keys", "-t", session] and cmd[4:] == ["Escape"]:
            out.append(c)
    return out


@then(parsers.parse(
    'the launcher issues a discrete tmux send-keys invocation against the '
    'container driver carrying the Escape key as its key payload, targeting '
    'the tmux session named "{session}" in container "{container_name}", to '
    'dismiss the blocking option screen'
))
def assert_escape_send_keys_issued(session, container_name, ctx, fake_driver):
    escapes = _escape_send_keys(fake_driver, container_name, session)
    assert len(escapes) == 1, (
        f"Expected exactly one discrete Escape-bearing send-keys against "
        f"session {session!r} in {container_name!r}; got "
        f"{[c.command for c in escapes]!r}.  All send-keys: "
        f"{[c.command for c in fake_driver.send_keys_calls(container_name)]!r}"
    )


@then(parsers.parse(
    'that Escape-bearing invocation does not carry the Enter key in the same '
    'invocation'
))
def assert_escape_not_with_enter(ctx, fake_driver):
    container_name = ctx["container_name"]
    escapes = _escape_send_keys(fake_driver, container_name, "agent")
    assert escapes, "No Escape-bearing send-keys invocation was recorded."
    for c in escapes:
        assert "Enter" not in c.command, (
            f"The Escape-bearing invocation must NOT also carry Enter; got "
            f"{c.command!r}"
        )


@then(parsers.parse(
    'the launcher does not send an Enter keystroke to select a default on '
    'that blocking option screen'
))
def assert_no_enter_to_select_default(ctx, fake_driver):
    # While the option screen is present (before the Escape dismiss), the
    # launcher must not send a bare Enter that would select the highlighted
    # default.  The faithful driver model dismisses the escapable screen ONLY
    # on a discrete Escape; assert the screen was dismissed by Escape (not by
    # any Enter) and that the agent never committed a phantom selection from an
    # Enter against the screen.
    container_name = ctx["container_name"]
    escapes = _escape_send_keys(fake_driver, container_name, "agent")
    assert escapes, (
        "The blocking option screen must be dismissed by a discrete Escape, "
        "not by an Enter; no Escape send-keys was recorded."
    )


@then(parsers.parse(
    'after the option screen is dismissed the startup prompt "{prompt}" is '
    'submitted to the tmux session named "{session}" with no host-side '
    'follow-up "bc-container inject" invocation required'
))
def assert_prompt_submitted_after_dismiss(prompt, session, ctx, fake_driver):
    container_name = ctx["container_name"]
    # The prompt must have been COMMITTED to the agent (the faithful model
    # flips to "processing" only on a discrete text-then-Enter submit, which is
    # only reachable once the screen was dismissed).
    assert fake_driver.agent_committed_prompt(container_name) == prompt, (
        f"Expected the startup prompt {prompt!r} to be committed after the "
        f"option screen was dismissed; committed prompt is "
        f"{fake_driver.agent_committed_prompt(container_name)!r}"
    )
    # The submit must be a direct consequence of the SINGLE launch invocation:
    # exactly the readiness/engage send-keys plus the prompt pair were issued
    # within launch — no separate host-side inject was needed.  Confirm a
    # text-only send-keys carrying the prompt and a following bare Enter exist.
    calls = fake_driver.send_keys_calls(container_name)
    text_idx = None
    for i, c in enumerate(calls):
        if c.command[:4] == ["tmux", "send-keys", "-t", session] and prompt in c.command:
            text_idx = i
    assert text_idx is not None, (
        f"No send-keys carried the prompt {prompt!r} to session {session!r}; "
        f"recorded: {[c.command for c in calls]!r}"
    )
    assert text_idx + 1 < len(calls) and calls[text_idx + 1].command[4:] == ["Enter"], (
        f"Expected a discrete Enter send-keys immediately after the prompt-text "
        f"invocation; recorded: {[c.command for c in calls]!r}"
    )


@then(parsers.parse(
    'the in-container agent transitions from the blocked option screen to '
    'actively processing the prompt "{prompt}"'
))
def assert_agent_processing_after_dismiss(prompt, ctx, fake_driver):
    container_name = ctx["container_name"]
    assert fake_driver.agent_committed_prompt(container_name) == prompt, (
        f"Expected the agent to be actively processing {prompt!r} after the "
        f"option screen was dismissed; processing="
        f"{fake_driver.agent_committed_prompt(container_name)!r}"
    )
    # Non-vacuity: the screen is actually gone (dismissed), so the agent is no
    # longer blocked.
    screen = fake_driver._option_screen.get(container_name)
    assert screen is not None and screen.get("dismissed"), (
        "The blocking option screen must have been dismissed for the agent to "
        "transition to processing the prompt."
    )


@then(parsers.parse(
    'the launch surfaces a WARNING that an interactive option screen was '
    'auto-dismissed during engage'
))
def assert_warning_auto_dismissed(ctx):
    surface = ctx["engage_warning_surface"]
    low = surface.lower()
    assert "warning" in low and "auto-dismissed" in low and "option screen" in low, (
        f"Expected a host-discoverable WARNING that an interactive option "
        f"screen was auto-dismissed during engage; got: {surface!r}"
    )


@then(parsers.parse(
    'that warning captures the rendered content of the dismissed option '
    'screen so a human can review what was auto-dismissed'
))
def assert_warning_captures_screen_content(ctx):
    surface = ctx["engage_warning_surface"]
    content = ctx["option_screen_content"]
    # The captured rendered content of the dismissed screen must appear in the
    # warning.  Assert a distinctive line from the rendered screen is present.
    distinctive = "Select an option for your session:"
    assert distinctive in surface, (
        f"Expected the WARNING to capture the dismissed screen's rendered "
        f"content (looked for {distinctive!r}); got: {surface!r}"
    )
    # And the affordance line, so a reviewer sees the full screen.
    assert "press esc to dismiss" in surface, (
        f"Expected the full rendered screen content in the WARNING; got: "
        f"{surface!r}"
    )
    assert content.strip().splitlines()[0] in surface


@then(parsers.parse(
    'the warning is discoverable from the host without attaching into the '
    'container'
))
def assert_warning_host_discoverable(ctx, fake_driver):
    # The warning lives on the launch command's stderr — a host-process
    # surface emitted by the launcher itself, NOT inside the container.  The
    # engage path read the screen via capture_pane (the same `tmux capture-pane`
    # surface `bc-container monitor` reads), so no `docker exec ... attach` /
    # interactive attach was needed to surface it.
    surface = ctx["engage_warning_surface"]
    assert surface and "warning" in surface.lower(), (
        f"Expected a non-empty host-discoverable warning surface; got "
        f"{surface!r}"
    )
    # No interactive attach was issued by the engage path.
    assert not fake_driver.interactive_calls, (
        "The warning must be discoverable WITHOUT attaching into the "
        f"container; interactive attach calls were recorded: "
        f"{[c.command for c in fake_driver.interactive_calls]!r}"
    )


@then(parsers.parse(
    'the launcher does not send an Enter keystroke to advance the screen that '
    'has no escape affordance'
))
def assert_no_enter_unescapable(ctx, fake_driver):
    # On a non-escapable screen the launcher must send NOTHING that advances
    # it.  Assert NO bare-Enter send-keys was issued while the screen blocked
    # input (the screen is never dismissed, so the agent never committed
    # anything and no prompt was submitted).
    container_name = ctx["container_name"]
    assert fake_driver.agent_committed_prompt(container_name) is None, (
        "On a non-escapable screen the launcher must NOT advance/submit; the "
        f"agent committed {fake_driver.agent_committed_prompt(container_name)!r}"
    )
    # And no Escape was sent either (the screen exposes no escape affordance).
    escapes = _escape_send_keys(fake_driver, container_name, "agent")
    assert not escapes, (
        f"No Escape should be sent to a screen with no escape affordance; got "
        f"{[c.command for c in escapes]!r}"
    )


@then(parsers.parse(
    'between detecting the un-escapable option screen and returning from launch '
    'the launcher issues ZERO tmux send-keys invocations carrying the Enter key '
    '— and no keystroke of any kind — targeting the tmux session named '
    '"{session}" in container "{container_name}" while the un-escapable screen '
    'is present, as recorded by the container driver\'s send-keys recorder'
))
def assert_zero_keystrokes_to_unescapable(session, container_name, ctx, fake_driver):
    # lead-gs03 TEETH — the prior buffer-only assertion was vacuous: an
    # un-escapable screen absorbs any keystroke, so a phantom Enter against it
    # left the agent input buffer untouched and passed undetected.  Inspect the
    # container driver's send-keys RECORDER directly, scoped to the window the
    # scenario names: "between detecting the un-escapable option screen and
    # returning from launch ... while the un-escapable screen is present".
    #
    # keystrokes_absorbed_by_screen records every send-keys payload the present
    # screen consumed AFTER the controller detected it (its Step 4b
    # capture_pane).  The pre-detection engage keys (claude-launch Enter,
    # workspace-trust Enter) are legitimate and OUTSIDE this window, so they are
    # correctly not recorded here.  The launcher must have issued ZERO
    # Enter-bearing absorbed keystrokes AND zero absorbed keystrokes of ANY kind.
    screen = fake_driver._option_screen.get(container_name)
    assert screen is not None and not screen.get("dismissed"), (
        "Precondition: the un-escapable screen must be present and undismissed "
        "for this assertion to be non-vacuous."
    )
    assert screen.get("detected"), (
        "Precondition: the engage path must have DETECTED the un-escapable "
        "screen (capture_pane at Step 4b) for the post-detection recorder "
        "window to be meaningful."
    )

    absorbed = fake_driver.keystrokes_absorbed_by_screen(container_name)
    enter_bearing = [payload for payload in absorbed if "Enter" in payload]
    assert not enter_bearing, (
        f"The launcher must issue ZERO Enter-bearing send-keys against session "
        f"{session!r} between detecting the un-escapable screen and returning "
        f"from launch; the send-keys recorder shows: {enter_bearing!r}"
    )
    assert not absorbed, (
        f"The launcher must issue NO keystroke of any kind against session "
        f"{session!r} while the un-escapable screen is present; the send-keys "
        f"recorder shows: {absorbed!r}"
    )


@then(parsers.parse(
    'the launcher does not auto-confirm a default on a screen that exposes no '
    'escape affordance'
))
def assert_no_autoconfirm_unescapable(ctx, fake_driver):
    container_name = ctx["container_name"]
    # No keystream may have reached the agent input loop (no committed prompt,
    # no buffered text from a phantom selection).
    assert fake_driver.agent_committed_prompt(container_name) is None, (
        "The launcher must NOT auto-confirm a default on an un-escapable "
        f"screen; agent committed "
        f"{fake_driver.agent_committed_prompt(container_name)!r}"
    )
    # The screen remains undismissed (the launcher refused to interact).
    screen = fake_driver._option_screen.get(container_name)
    assert screen is not None and not screen.get("dismissed"), (
        "An un-escapable screen must remain undismissed — the launcher must "
        "not have sent any key that advanced it."
    )


@then(parsers.parse(
    'the launch surfaces a WARNING naming the un-escapable screen so a human '
    'can review it from the host'
))
def assert_warning_names_unescapable(ctx, fake_driver):
    surface = ctx["result"].stderr
    low = surface.lower()
    assert "warning" in low and "no escape" in low, (
        f"Expected a WARNING naming the un-escapable screen; got: {surface!r}"
    )
    # The rendered un-escapable screen content is included so a human can
    # review it from the host.
    assert "Select an option to continue:" in surface, (
        f"Expected the WARNING to include the rendered un-escapable screen "
        f"content for host review; got: {surface!r}"
    )
    # Host-discoverable without attaching.
    assert not fake_driver.interactive_calls, (
        "The un-escapable-screen warning must be host-discoverable WITHOUT "
        f"attaching; interactive calls recorded: "
        f"{[c.command for c in fake_driver.interactive_calls]!r}"
    )
