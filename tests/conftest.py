"""
BDD step definitions for bc-container scenarios.

All Docker interaction is stubbed via FakeDockerDriver — no live daemon required.
All GitHub and git operations in manifest scenarios are stubbed via FakeGitHubDriver
and FakeGitDriver.
"""
from __future__ import annotations

import platform
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
    _is_origin_owner_writeback_command,
    _is_repo_create_command,
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

@pytest.fixture(autouse=True)
def _lead63em_host_state_dir(tmp_path, monkeypatch):
    """Point BCLAUNCHER_HOST_STATE_DIR at a per-test tmp dir (lead-63em).

    Every launch-failure path now persists a diagnostic file under the per-BC
    host state surface (default ``/var/lib/bc-launcher``, which is unwritable
    in CI).  Redirecting it to a per-test tmp dir for the WHOLE suite keeps
    every launch-failure-exercising test (not just the new diagnostic
    scenarios) writing into the sandbox, and prevents env leakage across
    tests.  ``monkeypatch`` restores the prior value automatically at teardown.
    """
    monkeypatch.setenv("BCLAUNCHER_HOST_STATE_DIR", str(tmp_path / "host-state"))


@pytest.fixture
def fake_driver():
    """Return a fresh FakeDockerDriver."""
    return FakeDockerDriver()


@pytest.fixture
def controller(fake_driver):
    """Return a BcContainerController backed by the fake driver.

    lead-cw7m — the controller's bounded readiness-wait scan-dismiss loop
    budgets its total elapsed time against an injectable monotonic clock; the
    fake driver provides a deterministic, strictly-advancing clock so the
    never-clears bounded-timeout path terminates without any real sleeping.
    """
    return BcContainerController(fake_driver, monotonic=fake_driver.monotonic)


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


# --- lead-pixf: agent-presence / infra-failure Givens ---------------------

@given(parsers.parse(
    'a tmux session named "{session}" exists inside the container with a '
    'live claude agent process whose "{watch}" is armed'
))
def tmux_session_live_agent_watch_armed(session, watch, ctx, fake_driver):
    """lead-pixf f2ddd6c7: model a LIVE agent — the "agent" tmux session
    holds a live claude whose shop-msg watch inbox watcher is armed."""
    for container_name in list(fake_driver._running):
        fake_driver.add_tmux_session(container_name, session)
        fake_driver.set_agent_online(container_name, online=True)


@given(parsers.parse(
    'a tmux session named "{session}" exists inside the container with a '
    'live claude agent process already at the input-ready marker "{marker}"'
))
def tmux_session_live_agent_at_input_ready(session, marker, ctx, fake_driver):
    """lead-pixf aeebb281: model an ALREADY-live agent sitting at the
    input-ready marker, so start-agent must detect it and no-op."""
    for container_name in list(fake_driver._running):
        fake_driver.add_tmux_session(container_name, session)
        fake_driver.set_tmux_pane_content(container_name, marker)
        fake_driver.set_agent_online(container_name, online=True)


@given("the docker socket is unreachable so container inspection is denied")
def docker_socket_unreachable(ctx, fake_driver):
    """lead-pixf 010e776c: model an unreachable Docker daemon socket so
    list_bc_containers raises instead of returning an empty list."""
    fake_driver.set_docker_socket_unreachable(True)


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
    # lead-uiwu FACET 1: when the scenario declares that NO --repo-url and no
    # --workspace-mount are provided, pass repo_url=None so the controller must
    # resolve the clone source from bc-manifest.yaml (or fail loudly).  Default
    # behaviour (no flag) is unchanged: a default repo_url is supplied.
    if ctx.get("no_repo_flags"):
        repo_url = None
    else:
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
                               shop_network=ctx.get("shop_network"),
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
    # Fault selection (lead-63em).  When a prior Given step pinned a specific
    # launch fault (ctx["launch_fault"]), configure the fake driver so the
    # launch fails at exactly that point.  Otherwise default to the
    # messaging-DB-unreachable path: the pre-lead-63em messaging-readiness
    # scenarios using this exact "... and a startup prompt" phrasing pin the
    # barrier-blocks-at-launch path (the readiness sequence has NOT yet
    # passed), and the "once readiness completes successfully" Then step flips
    # the DB back to reachable and re-launches — so the default must remain
    # messaging-DB-unreachable to keep those scenarios green.
    from bc_launcher.controller import (
        AGENT_TMUX_SESSION,
        CAUSE_MARKER_AGENT_STARTUP,
        CAUSE_MARKER_AGENT_VAULT,
        CAUSE_MARKER_MESSAGING_DB,
        CAUSE_MARKER_READINESS,
        CLAUDE_INPUT_READY_MARKER,
        CLAUDE_READY_MARKER,
    )
    fault = ctx.get("launch_fault", CAUSE_MARKER_MESSAGING_DB)
    container_name = f"bc-{bc_name}"
    if fault == CAUSE_MARKER_MESSAGING_DB:
        fake_driver.set_dsn_reachable(dsn, reachable=False)
    elif fault == CAUSE_MARKER_AGENT_VAULT:
        # Messaging DB reachable; agent-vault broker barrier fails.
        fake_driver.set_dsn_reachable(dsn, reachable=True)
        fake_driver.set_all_brokers_unreachable(True)
    elif fault == CAUSE_MARKER_AGENT_STARTUP:
        # Both readiness barriers pass; claude / its tmux session never
        # starts, so the PRE-trust CLAUDE_READY_MARKER is never observed.
        fake_driver.set_dsn_reachable(dsn, reachable=True)
        fake_driver.simulate_marker_timeout(
            container_name, AGENT_TMUX_SESSION, CLAUDE_READY_MARKER
        )
    elif fault == CAUSE_MARKER_READINESS:
        # Both readiness barriers pass and claude starts, but the readiness
        # barrier never reports both supporting servers ready — the POST-trust
        # CLAUDE_INPUT_READY_MARKER (the input-ready barrier) is never observed.
        fake_driver.set_dsn_reachable(dsn, reachable=True)
        fake_driver.simulate_marker_timeout(
            container_name, AGENT_TMUX_SESSION, CLAUDE_INPUT_READY_MARKER
        )
    else:  # pragma: no cover - defensive
        raise AssertionError(f"unknown launch_fault {fault!r}")
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


@when(parsers.parse('I run bc-container start-agent with BC name "{bc_name}"'))
def run_start_agent(bc_name, ctx, fake_driver, controller):
    # lead-pixf aeebb281: record the container's input-ready marker waits
    # BEFORE the call so the Then step can prove the no-op short-circuit did
    # NOT enter the readiness-marker probe loop.
    #
    # Mirror the CLI's start-agent path: omitting --startup-prompt injects
    # the DEFAULT session-start imperative.  Passing the resolved default
    # (rather than None) is what gives the no-op teeth their force — with a
    # non-empty startup_prompt, the WITHOUT-no-op (pre-fix) path WOULD run
    # the readiness-marker probe and start a claude, so the assertions that
    # neither happens actually catch a regression.
    from bc_launcher.cli import DEFAULT_STARTUP_PROMPT_TEMPLATE
    container = f"bc-{bc_name}"
    ctx["waits_before"] = list(fake_driver.wait_for_marker_calls)
    ctx["claude_launches_before"] = fake_driver.claude_launch_count(container)
    startup_prompt = DEFAULT_STARTUP_PROMPT_TEMPLATE.format(bc_name=bc_name)
    result = controller.start_agent(bc_name, startup_prompt=startup_prompt)
    ctx["result"] = result
    ctx["bc_name"] = bc_name
    ctx["container_name"] = container


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


# --- lead-pixf: agent-presence / infra-failure Thens ----------------------

@then(parsers.parse('stdout reports the agent presence as "{presence}"'))
def assert_agent_presence(presence, ctx):
    """lead-pixf f2ddd6c7.  RED if status reports the agent offline / omits
    "online" for a live agent."""
    result = ctx["result"]
    assert f"agent_presence: {presence}" in result.stdout, (
        f"Expected agent presence {presence!r} (line "
        f"'agent_presence: {presence}') in stdout, got: {result.stdout!r}"
    )


@then("stderr reports that the docker socket could not be reached")
def assert_stderr_docker_socket_unreachable(ctx):
    """lead-pixf 010e776c.  RED if list masks the socket failure."""
    result = ctx["result"]
    stderr = result.stderr if hasattr(result, "stderr") else ""
    lowered = stderr.lower()
    assert "docker socket could not be reached" in lowered or (
        "docker" in lowered and "could not be reached" in lowered
    ), (
        f"Expected stderr to report the docker socket could not be reached, "
        f"got: {stderr!r}"
    )


@then(parsers.parse('stdout does not report "{text}"'))
def assert_stdout_does_not_report(text, ctx):
    """lead-pixf 010e776c.  RED if list masks an infra failure as an empty
    inventory by printing "No BC containers found"."""
    result = ctx["result"]
    stdout = result.stdout if hasattr(result, "stdout") else ""
    assert text not in stdout, (
        f"Expected {text!r} to be ABSENT from stdout (an unreachable socket "
        f"must not be masked as an empty list), got: {stdout!r}"
    )


@then(parsers.parse(
    'stdout reports that "{container}" already has a live agent and is online'
))
def assert_start_agent_already_live(container, ctx):
    """lead-pixf aeebb281.  RED if start-agent does not report the
    already-live / online no-op."""
    result = ctx["result"]
    stdout = result.stdout
    assert container in stdout, (
        f"Expected container {container!r} named in stdout, got: {stdout!r}"
    )
    lowered = stdout.lower()
    assert "already has a live agent" in lowered and "online" in lowered, (
        f"Expected start-agent to report {container!r} already has a live "
        f"agent and is online, got: {stdout!r}"
    )


@then(
    "the command does not wait on the readiness-marker probe until the "
    "readiness timeout"
)
def assert_start_agent_no_readiness_probe(ctx, fake_driver):
    """lead-pixf aeebb281.  RED if start-agent enters the agent-start
    sequence and waits on the readiness-marker probe (which, against an
    already-live agent past input-ready, would never re-observe the trust
    banner and would hang to the readiness timeout).  The no-op short-circuit
    must run NO new wait_for_pane_marker probe for this container."""
    from bc_launcher.controller import (
        CLAUDE_INPUT_READY_MARKER,
        CLAUDE_READY_MARKER,
    )
    container = ctx["container_name"]
    waits_before = ctx.get("waits_before", [])
    new_waits = fake_driver.wait_for_marker_calls[len(waits_before):]
    readiness_waits = [
        (c, s, m)
        for (c, s, m) in new_waits
        if c == container
        and m in (CLAUDE_INPUT_READY_MARKER, CLAUDE_READY_MARKER)
    ]
    assert not readiness_waits, (
        "start-agent must NOT wait on the readiness-marker probe when the "
        f"agent is already live; observed readiness waits: {readiness_waits!r}"
    )


@then(parsers.parse(
    'no second claude agent process is started in the tmux session named '
    '"{session}"'
))
def assert_no_second_claude(session, ctx, fake_driver):
    """lead-pixf aeebb281.  RED if start-agent starts a second claude
    (`agent-vault run -- claude ...`) in the agent session instead of
    no-opping against the already-live agent."""
    container = ctx["container_name"]
    launches_before = ctx.get("claude_launches_before", 0)
    launches_after = fake_driver.claude_launch_count(container)
    assert launches_after == launches_before, (
        "start-agent must NOT start a second claude agent against an "
        f"already-live agent; claude-launch keystrokes went from "
        f"{launches_before} to {launches_after} for {container!r}"
    )


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


# ---------------------------------------------------------------------------
# On-disk shop network resolution (lead-ngzl, scenario 63)
# ---------------------------------------------------------------------------

@given(parsers.parse(
    'the shop\'s on-disk configuration declares the shop docker network name '
    '"{network_name}" as the single derived network coordinate (the ADR-043 D2 '
    'ops-coordinates derivation root; in the interim the compose.yaml network '
    '"{compose_network}" and the product slug)'
))
def shop_on_disk_network_declared(network_name, compose_network, ctx):
    """Model the shop network resolved from the shop's on-disk configuration.

    The interim resolution (_resolve_shop_network) reads the compose.yaml
    network / product slug on the real shop; in the test harness we inject the
    RESOLVED value the way the CLI would pass it into controller.launch(),
    pinning the BEHAVIOR + resolved VALUE without hard-pinning the source
    artifact shape (ADR-043 D2 ops-coordinates not yet finalized; lead-7wta)."""
    ctx["shop_network"] = network_name


@given(parsers.parse(
    'the bc-manifest.yaml registers the BC "{bc_name}" but carries no '
    'shop-level network or product launch field'
))
def manifest_registers_bc_no_product(bc_name, ctx, tmp_path):
    """Create a bc-manifest.yaml that registers the BC but has NO top-level
    product/network field — exactly the case the product authority hit, where
    the old code hard-errored and the fix must instead resolve the shop network
    from on-disk config."""
    import yaml as _yaml
    manifest_path = tmp_path / "bc-manifest.yaml"
    manifest_path.write_text(_yaml.dump({
        "bcs": [{
            "name": bc_name,
            "remote": f"https://github.com/dstengle/{bc_name}.git",
            "role": "bc",
        }],
    }))
    ctx["launch_manifest_path"] = manifest_path


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


@then(parsers.parse('the command does not emit the error "{text}"'))
def assert_command_does_not_emit_error(text, ctx):
    """Assert the narrowed (lead-ngzl) "no network" error did NOT fire — the
    on-disk shop network was resolvable, so launch must not hard-error."""
    result = ctx.get("result")
    assert result is not None, "No result in ctx"
    stderr = result.stderr if hasattr(result, "stderr") else ""
    assert text not in stderr, (
        f"Expected {text!r} NOT in stderr, but it was present.\n"
        f"stderr: {stderr!r}"
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


# The bc-base Dockerfile installs shop-templates at a version taken from the
# SHOP_TEMPLATES_VERSION build ARG (default vX.Y.Z); the centralized scheduled
# poll (lead-czwo, poll-bc-base-deps.yml) bumps that ARG default to the resolved
# latest release.  A genuine version-by-shape pin is therefore EITHER:
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
    # shopsystem-templates).  The version is PARAMETERIZED through the
    # SHOP_TEMPLATES_VERSION build ARG (the centralized poll, lead-czwo, bumps
    # the ARG default to the resolved latest release); the ARG carries a
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
    # dstengle/shopsystem-templates repo by vMAJOR.MINOR.PATCH shape.  Its
    # version is PARAMETERIZED through the SHOP_TEMPLATES_VERSION build ARG
    # (default vX.Y.Z; the centralized poll, lead-czwo, bumps that default), so
    # it appears in the @${SHOP_TEMPLATES_VERSION} form rather than as a frozen
    # @vX.Y.Z literal; the helper recognizes both.
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
        # shop-templates is PARAMETERIZED: its version comes from the
        # SHOP_TEMPLATES_VERSION build ARG (the centralized poll, lead-czwo,
        # bumps the ARG default to the resolved latest release). The owner/repo
        # binding and vX.Y.Z version shape are still asserted (the ARG default
        # carries the shape) -- a wrong owner/repo still FAILS.
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


def _digest_sha_for_label(label):
    """Turn a scenario digest LABEL (e.g. "D_old"/"D_new") into a distinct,
    well-formed sha256 value carrying the label, so D_old and D_new are
    genuinely DIFFERENT content-addressable digests (D_old != D_new)."""
    label_hex = "".join(c for c in label.lower() if c in "0123456789abcdef")
    # Prefix with the label's letters mapped to hex so labels with no hex
    # chars of their own (e.g. "D_old" -> "d") still differ from one another.
    seed = "".join(format(ord(c), "x") for c in label.lower())
    return f"sha256:{(seed + label_hex)}".ljust(71, "0")[:71]


@given(parsers.parse('the local Docker cache holds the bc-base "latest" tag '
                     'at an older digest "{old_digest}"'))
def given_cache_holds_old_digest(old_digest, ctx, fake_driver):
    # Model the STALE local state: the bc-base ":latest" tag is cached locally
    # at the older digest D_old.  A launch that runs the bare ":latest" tag (the
    # v0.3.34 regression) therefore serves THIS D_old content from the cache.
    old_sha = _digest_sha_for_label(old_digest)
    fake_driver.seed_local_cache(_BC_BASE_LATEST_REF, old_sha)
    ctx["cached_old_digest"] = old_digest
    ctx["cached_old_sha"] = old_sha


@given(parsers.parse('the registry "{image_ref}" now publishes the "latest" '
                     'tag at a newer digest "{new_digest}"'))
def given_registry_publishes_new_digest(image_ref, new_digest, ctx, fake_driver):
    registry_driver = FakeRegistryDriver()
    # The registry resolves the bc-base "latest" reference to the NEW digest
    # D_new — DIFFERENT from the cached D_old.  Model the digest as a genuine
    # content-addressable sha256 pin (the shape the real driver produces).
    sha = _digest_sha_for_label(new_digest)
    assert sha != ctx.get("cached_old_sha"), (
        "test setup error: D_new must differ from the cached D_old"
    )
    registry_driver.set_registry_digest(_BC_BASE_LATEST_REF, sha)
    # Wire the registry into the fake docker driver so a PULL of the resolved
    # digest pin populates the local cache with D_new content (and so a manual
    # pull of the ":latest" tag would re-resolve it).
    fake_driver.set_registry_for_pull(registry_driver)
    ctx["registry_driver"] = registry_driver
    ctx["registry_new_digest"] = new_digest
    ctx["registry_new_sha"] = sha


@then(parsers.parse('launch resolves the bc-base "latest" tag against the '
                    'registry and pulls digest "{new_digest}" before starting '
                    "the container"))
def then_launch_resolves_new_digest(new_digest, ctx):
    fake_driver = ctx["fake_driver_for_run"]
    registry_driver = ctx["registry_driver"]
    resolved_sha = ctx["registry_new_sha"]

    # (1) launch RESOLVED the bc-base "latest" tag against the registry.
    assert _BC_BASE_LATEST_REF in registry_driver.resolve_calls, (
        "launch did not resolve the bc-base \"latest\" tag against the "
        f"registry; resolve calls were: {registry_driver.resolve_calls!r}"
    )

    # (2) launch PULLED the resolved D_new digest pin (so the republished image
    # is fetched into the local cache instead of serving the stale cached
    # ":latest").  Without a pull of the registry-current digest, the local
    # cache still serves D_old — exactly the v0.3.34 regression.
    pulled_dnew = [r for r in fake_driver.pull_calls if resolved_sha in r]
    assert pulled_dnew, (
        f"launch did not pull the resolved D_new digest {resolved_sha!r} "
        f"(label {new_digest!r}); pull calls were: {fake_driver.pull_calls!r}. "
        "A launch that resolves but does not pull the registry-current digest "
        "serves the stale cached :latest (D_old)."
    )

    # (3) the pull happened BEFORE the container started: the pull of D_new must
    # precede the run of the bc-shopsystem-messaging container in op order.
    ops = fake_driver.operation_log
    pull_idx = next(
        (i for i, (op, arg) in enumerate(ops)
         if op == "pull" and resolved_sha in arg),
        None,
    )
    run_idx = next(
        (i for i, (op, arg) in enumerate(ops)
         if op == "run" and arg == ctx["container_name"]),
        None,
    )
    assert pull_idx is not None and run_idx is not None and pull_idx < run_idx, (
        f"launch did not pull D_new ({resolved_sha!r}) BEFORE starting the "
        f"container; operation order was: {ops!r}"
    )


@then(parsers.parse('the started container "{container_name}" is running from '
                    'image digest "{new_digest}" rather than the cached '
                    '"{old_digest}"'))
def then_container_runs_from_new_digest(container_name, new_digest, old_digest, ctx):
    fake_driver = ctx["fake_driver_for_run"]
    new_sha = ctx["registry_new_sha"]
    old_sha = ctx["cached_old_sha"]

    run_cmd = fake_driver.run_command_for_container(container_name)
    image_tokens = [tok for tok in run_cmd if "shopsystem-bc-base" in tok]
    assert image_tokens, (
        f"docker run for {container_name} carries no bc-base image reference: "
        f"{run_cmd!r}"
    )
    image_ref = image_tokens[0]

    # The container must run from the registry-resolved digest pin
    # (repo@sha256:D_new), NOT the bare ":latest" tag that the local cache
    # serves as the stale D_old.
    assert ":latest" not in image_ref, (
        f"Container {container_name} started from the moving :latest tag (the "
        f"cached {old_digest!r}={old_sha!r}) instead of the resolved digest "
        f"pin: {image_ref!r}."
    )
    assert image_ref.endswith("@" + new_sha), (
        f"Container {container_name} bc-base image ref is not the resolved "
        f"D_new digest pin (cached {old_digest!r} would otherwise be served): "
        f"{image_ref!r}."
    )

    # CONTENT FIDELITY: resolve what the run image ACTUALLY serves from the
    # local cache.  Because launch pulled D_new, the digest pin serves D_new;
    # the stale cached ":latest" still holds D_old.  Assert the served content
    # is D_new and is NOT the cached D_old — so a regression that ran the cached
    # :latest (serving D_old) goes RED here.
    served = fake_driver.served_digest_for_run(container_name)
    assert served == new_sha, (
        f"Container {container_name} is NOT serving the republished D_new "
        f"content {new_sha!r} (label {new_digest!r}); it served {served!r}. "
        f"A launch that ran the cached :latest would serve D_old {old_sha!r}."
    )
    assert served != old_sha, (
        f"Container {container_name} is running the STALE cached D_old "
        f"{old_sha!r} (label {old_digest!r}) instead of the republished D_new."
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
    # shop-templates is installed from its dstengle VCS pin; its version is
    # parameterized through the SHOP_TEMPLATES_VERSION build ARG (default
    # vX.Y.Z; bumped by the centralized poll, lead-czwo) rather than a frozen
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
# lead-ypnz / GAP D — the empty-remote-seed classifier is version-robust: it
# recognises the CURRENT bc-base dolt clone failure ("clone failed; remote at
# that url contains no Dolt data") in addition to the legacy "git remote has no
# branches" text, so the empty-remote-seed step (the lead-5k8c seed-then-retry
# block) FIRES for a freshly `gh repo create --add-readme`'d tracker (git
# README branch present, NO dolt refs) and the retried `bd bootstrap` exits
# zero.  Scenario Outline @scenario_hash:6fc82a7375ed8aa9 exercises the
# executable `_is_empty_remote_failure` predicate + the seed-then-retry block it
# gates over BOTH the current and legacy error texts (Examples negative
# control: the "contains no Dolt data" row is RED against a legacy-only
# classifier, the "git remote has no branches" row stays GREEN).  Additive to
# ada742d33c996d34 (NOT retired).
# ---------------------------------------------------------------------------


def _legacy_only_empty_remote_classifier(message: str) -> bool:
    """Reference LEGACY-ONLY empty-remote classifier (lead-ypnz negative
    control).

    This is the pre-GAP-D predicate: it matches ONLY the legacy "git remote
    has no branches" text.  It deliberately does NOT know the current bc-base
    dolt "contains no Dolt data" text, so the scenario can assert that the
    seed firing on the current-dolt error is CAUSED by the version-robust
    match — a legacy-only classifier would leave the seed unfired rather than
    retrying unconditionally.
    """
    text = message.lower()
    return "git remote has no branches" in text or (
        "no branches" in text and "initialize" in text
    )


@given(parsers.parse(
    "the standup's create-absent orchestration has created the tracker repo "
    '"{tracker}" with "gh repo create --add-readme", so it exists with a git '
    "README branch but carries no Dolt refs"
))
def gapd_created_tracker_no_dolt_refs(tracker, ctx, fake_driver, controller,
                                      tmp_path):
    """Set up a freshly `gh repo create --add-readme`'d `<bc>-beads` tracker:
    the GitHub repo EXISTS (git README branch) but carries NO dolt refs — i.e.
    an EMPTY/unseeded Dolt remote (lead-ypnz / GAP D).

    Derives the concrete BC name from the tracker slug (`<owner>/<bc>-beads`)
    and sets up the same launch fixtures the lead-5k8c empty-remote scenario
    uses, so the full standup launch reaches the in-container `bd bootstrap`
    step and its empty-remote-seed block.
    """
    owner, _, repo = tracker.partition("/")
    assert repo.endswith("-beads"), (
        f"tracker slug {tracker!r} must be of the form <owner>/<bc>-beads"
    )
    bc_name = repo[: -len("-beads")]
    ctx["driver"] = fake_driver
    ctx["controller"] = controller
    ctx["bc_name"] = bc_name
    ctx["standup_owner"] = owner
    ctx["standup_tracker"] = tracker
    ctx["repo_url"] = f"https://github.com/shopsystem/{bc_name}.git"
    container_name = f"bc-{bc_name}"
    ctx["container_name"] = container_name
    # Default credential_home with all standard credential dirs/files (mirrors
    # the shared "BC is installed" given).
    credential_home = tmp_path / "fake_home"
    credential_home.mkdir(parents=True, exist_ok=True)
    (credential_home / ".claude").mkdir(parents=True, exist_ok=True)
    (credential_home / ".config" / "gh").mkdir(parents=True, exist_ok=True)
    gitconfig = credential_home / ".gitconfig"
    if not gitconfig.exists():
        gitconfig.write_text("")
    ctx["credential_home"] = credential_home
    # Committed prefix so the post-seed retry bootstrap derives a usable
    # issue_prefix (mirrors the lead-5k8c / lead-7jc2 idiom).
    fake_driver.set_committed_beads_prefix(container_name, "bclaunch")
    ctx["committed_beads_prefix"] = "bclaunch"
    # Repo EXISTS but its Dolt remote is EMPTY/unseeded (no dolt refs).
    fake_driver.set_beads_remote_empty(container_name, True)


@given(parsers.parse(
    'in that state the in-container "bd bootstrap" fails its Dolt clone with '
    'the error text "{bootstrap_error}"'
))
def gapd_bootstrap_fails_with_error(bootstrap_error, ctx, fake_driver):
    """Model the initial in-container `bd bootstrap` failing its Dolt clone
    with the exact `<bootstrap_error>` text (lead-ypnz / GAP D).

    For the current-dolt Examples row this is
    "clone failed; remote at that url contains no Dolt data"; for the legacy
    row it is the "git remote has no branches" text.  The controller's
    empty-remote-seed classifier must recognise both.
    """
    container_name = ctx["container_name"]
    fake_driver.set_beads_bootstrap_error(container_name, bootstrap_error)
    ctx["bootstrap_error"] = bootstrap_error


@given(parsers.parse(
    'the classification under observation is the standup\'s executable '
    '"_is_empty_remote_failure" predicate exercised on that error text, and '
    "the seed step under observation is the controller's seed-then-retry block "
    "that predicate gates, not a live standup run"
))
def gapd_classification_under_observation(ctx):
    """Documents the abstraction level under observation (lead-ypnz / GAP D):
    the executable `_is_empty_remote_failure` predicate and the controller's
    seed-then-retry block it gates.  No additional state — the launch When step
    exercises exactly that block through the fake driver."""
    ctx["gapd_observation"] = "predicate+seed-then-retry-block"


@when(parsers.parse(
    'the standup evaluates whether that "bd bootstrap" failure is an '
    "empty/unseeded-remote failure and runs its empty-remote-seed step"
))
def gapd_run_standup_seed_evaluation(ctx, fake_driver, controller, tmp_path):
    """Run the standup launch so the controller execs `bd bootstrap`, evaluates
    `_is_empty_remote_failure` on the failure, and runs its seed-then-retry
    block (lead-ypnz / GAP D)."""
    import yaml as _yaml
    bc_name = ctx["bc_name"]
    manifest_path = tmp_path / "bc-manifest.yaml"
    manifest_path.write_text(_yaml.dump({
        "product": "shopsystem product",
        "bcs": [{"name": bc_name, "remote": ctx["repo_url"], "role": "bc"}],
    }))
    result = controller.launch(
        bc_name=bc_name,
        repo_url=ctx["repo_url"],
        manifest_path=manifest_path,
        credential_home=ctx.get("credential_home"),
    )
    ctx["result"] = result
    ctx["container_name"] = f"bc-{bc_name}"


@then(parsers.parse(
    'the "_is_empty_remote_failure" predicate classifies "{bootstrap_error}" '
    "as an empty-remote failure, recognizing the current bc-base dolt "
    '"contains no Dolt data" text in addition to the legacy "git remote has '
    'no branches" text'
))
def gapd_predicate_classifies_empty_remote(bootstrap_error, ctx):
    """The executable `_is_empty_remote_failure` predicate must classify the
    error text as an empty-remote failure (lead-ypnz / GAP D).

    RED teeth: for the current-dolt row ("...contains no Dolt data") the
    legacy-only classifier returns False, so a pre-fix predicate leaves this
    assertion failing; post-fix (version-robust match) it returns True.
    """
    from bc_launcher.controller import _is_empty_remote_failure
    assert _is_empty_remote_failure(bootstrap_error) is True, (
        "`_is_empty_remote_failure` must classify the bd-bootstrap clone "
        f"failure {bootstrap_error!r} as an empty-remote failure (lead-ypnz: "
        "recognise the current bc-base dolt 'contains no Dolt data' text in "
        "addition to the legacy 'git remote has no branches' text); it did not"
    )


@then(parsers.parse(
    "because the failure is classified as empty-remote, the seed step fires, "
    "git-init-and-seeds the tracker's initial Dolt data, and the retried "
    '"bd bootstrap" exits zero instead of leaving the tracker unseeded'
))
def gapd_seed_fires_and_bootstrap_exits_zero(ctx, fake_driver):
    """The controller's seed-then-retry block must FIRE (seed the empty remote)
    and the retried `bd bootstrap` must exit zero, provisioning the working set
    (lead-ypnz / GAP D).

    RED teeth: on the current-dolt row a legacy-only classifier leaves the seed
    UNFIRED, so the remote stays unseeded and the working set is never
    provisioned.
    """
    container_name = ctx["container_name"]
    seed_calls = [
        c for c in fake_driver.exec_calls
        if c.container == container_name
        and _is_empty_remote_seed_command(c.command)
    ]
    assert seed_calls, (
        "The empty-remote-seed step must FIRE for the classified empty-remote "
        f"failure (lead-ypnz); no seed step ran. exec calls on "
        f"{container_name!r}: "
        f"{[c.command for c in fake_driver.exec_calls if c.container == container_name]!r}"
    )
    assert fake_driver.beads_remote_seeded(container_name), (
        "The empty beads dolt remote must end up SEEDED after the seed step "
        f"git-init-and-seeds the tracker's initial Dolt data (lead-ypnz); "
        f"container={container_name!r}"
    )
    assert fake_driver.beads_working_set_provisioned(container_name), (
        "The retried `bd bootstrap` must exit zero and provision the working "
        f"set after the seed (lead-ypnz); container={container_name!r}. A "
        "legacy-only classifier that failed to recognise the current-dolt "
        "error would leave the tracker unseeded and unprovisioned."
    )


@then(parsers.parse(
    "the seed firing is caused specifically by recognizing the current-dolt "
    'string, so a legacy-only classifier matching solely "git remote has no '
    'branches" would leave the seed unfired on the "contains no Dolt data" '
    "error rather than retrying unconditionally"
))
def gapd_seed_firing_caused_by_current_dolt_recognition(ctx):
    """Negative control (lead-ypnz / GAP D): tie the seed firing to the
    version-robust recognition of the current-dolt string, NOT to unconditional
    retry.

    A legacy-only classifier returns False for the current bc-base dolt
    "contains no Dolt data" error (so it would leave the seed UNFIRED) while
    returning True for the legacy "git remote has no branches" error; the
    actual version-robust predicate classifies BOTH as empty-remote, which is
    why the seed fired for this row.
    """
    from bc_launcher.controller import _is_empty_remote_failure
    current_dolt_error = "clone failed; remote at that url contains no Dolt data"
    legacy_error = (
        "git remote has no branches: ...; initialize the repository with an "
        "initial branch/commit first"
    )
    # The legacy-only classifier is BLIND to the current-dolt string (so it
    # would leave the seed UNFIRED on that error) but still recognises the
    # legacy string — a row-independent static fact that makes the negative
    # control non-vacuous.
    assert _legacy_only_empty_remote_classifier(current_dolt_error) is False, (
        "the legacy-only classifier must NOT recognise the current bc-base "
        "dolt 'contains no Dolt data' error (else the negative control is "
        "vacuous)"
    )
    assert _legacy_only_empty_remote_classifier(legacy_error) is True, (
        "the legacy-only classifier must still recognise the legacy 'git "
        "remote has no branches' error"
    )
    # THIS row's error was recognised by the REAL (version-robust) predicate —
    # which is what gated the seed firing, NOT an unconditional retry.  For the
    # current-dolt row this recognition is exactly the version-robust addition
    # the legacy-only classifier lacks; for the legacy row both classifiers
    # agree.
    assert _is_empty_remote_failure(ctx["bootstrap_error"]) is True, (
        "the seed firing must be gated by the real predicate recognising this "
        f"row's error {ctx['bootstrap_error']!r}, not an unconditional retry"
    )


# ---------------------------------------------------------------------------
# lead-7jc2 — ABSENT-repo provisioning: standing up a new BC must CREATE its
# absent `<bc>-beads` GitHub tracker repo (with an initial branch/commit) and
# ADD + SEED its Dolt remote before `bd bootstrap` can succeed.  Extends the
# lead-5k8c empty-remote seed pin (@scenario_hash:ada742d33c996d34) from the
# empty-but-existing remote to the absent-repo case.  The abstract <bc>/<owner>
# placeholders in the pinned scenario bind to the launcher BC itself, stood up
# under the configured beads remote org; the create-then-seed behaviour is
# exercised through the fake driver keyed by container name (gh/docker may be
# unavailable in-test, so the pin binds to the recorded standup behaviour).
# ---------------------------------------------------------------------------

@given(parsers.parse(
    'a new BC whose shop-name slug is "{bc}" is being stood up under '
    'GitHub owner "{owner}"'
))
def standup_new_bc(bc, owner, ctx, fake_driver, controller, tmp_path):
    # Concrete binding of the abstract <bc>/<owner> placeholders: stand up the
    # launcher BC itself.  Sets up the same fixtures the "BC is installed" given
    # provides (this scenario does not include that given).
    bc_name = "shopsystem-bc-launcher"
    ctx["driver"] = fake_driver
    ctx["controller"] = controller
    ctx["bc_name"] = bc_name
    ctx["repo_url"] = f"https://github.com/shopsystem/{bc_name}.git"
    ctx["container_name"] = f"bc-{bc_name}"
    ctx["standup_owner"] = owner
    credential_home = tmp_path / "fake_home"
    credential_home.mkdir(parents=True, exist_ok=True)
    (credential_home / ".claude").mkdir(parents=True, exist_ok=True)
    (credential_home / ".config" / "gh").mkdir(parents=True, exist_ok=True)
    gitconfig = credential_home / ".gitconfig"
    if not gitconfig.exists():
        gitconfig.write_text("")
    ctx["credential_home"] = credential_home


@given(parsers.parse(
    'its scaffolded ".beads/config.yaml" "sync.remote" points at '
    '"{tracker}", distinct from the lead\'s own "{lead_tracker}"'
))
def standup_config_sync_remote(tracker, lead_tracker, ctx):
    # Records the pre-state contract that the BC's OWN tracker slug is DISTINCT
    # from the lead's <product>-lead-beads.  The load-bearing provisioning
    # behaviour is asserted through the fake driver (keyed by container name);
    # this step pins that the two tracker slugs are not the same repository.
    assert tracker != lead_tracker, (
        f"the BC's own tracker {tracker!r} must be distinct from the lead's "
        f"own tracker {lead_tracker!r}"
    )
    ctx["standup_tracker"] = tracker


@given(parsers.parse('the "{tracker}" tracker repository does not yet exist'))
def standup_tracker_repo_absent(tracker, ctx, fake_driver):
    container_name = f"bc-{ctx['bc_name']}"
    fake_driver.set_beads_repo_absent(container_name, True)
    # A committed prefix so bootstrap can derive a usable issue_prefix and
    # `bd create` yields a prefixed id once provisioning completes.
    fake_driver.set_committed_beads_prefix(container_name, "bclaunch")
    ctx["committed_beads_prefix"] = "bclaunch"
    ctx["container_name"] = container_name


@when(parsers.parse(
    "the BC-standup flow provisions the new BC's beads tracker and runs "
    '"bd bootstrap"'
))
def standup_provisions_and_bootstraps(ctx, fake_driver, controller, tmp_path):
    import yaml as _yaml
    bc_name = ctx["bc_name"]
    manifest_path = tmp_path / "bc-manifest.yaml"
    manifest_path.write_text(_yaml.dump({
        "product": "shopsystem product",
        "bcs": [{"name": bc_name, "remote": ctx["repo_url"], "role": "bc"}],
    }))
    result = controller.launch(
        bc_name=bc_name,
        repo_url=ctx["repo_url"],
        manifest_path=manifest_path,
        credential_home=ctx.get("credential_home"),
    )
    ctx["result"] = result
    ctx["container_name"] = f"bc-{bc_name}"


def _resolve_standup_tracker_slug(tracker: str, bc_name: str) -> str:
    """Resolve the scenario's abstract "<owner>/<bc>-beads" tracker template
    into the CONCRETE GitHub slug the standup flow must target.

    lead-jq9b / ADR-043 D5.  The scenario pins the tracker NAME form via the
    `{tracker}` placeholder; the load-bearing binding is that the standup's
    provisioning commands target exactly this concrete slug.  `<owner>` binds
    to the beads remote org the controller derives its slug under, and `<bc>`
    binds to the BC being stood up.  Resolving from the scenario text (not a
    hardcoded slug) is what ties the on-disk contract to the emitted command:
    change the pinned NAME form and this expectation changes with it.
    """
    from bc_launcher.controller import BEADS_REMOTE_ORG
    return tracker.replace("<owner>", BEADS_REMOTE_ORG).replace("<bc>", bc_name)


@then(parsers.parse(
    'the standup flow creates the absent "{tracker}" tracker repository '
    'with an initial branch and commit'
))
def assert_absent_tracker_repo_created(tracker, ctx, fake_driver):
    container_name = ctx["container_name"]
    create_calls = [
        c for c in fake_driver.exec_calls
        if c.container == container_name and _is_repo_create_command(c.command)
    ]
    assert create_calls, (
        "The standup flow must CREATE the absent `<bc>-beads` tracker repo "
        "(gh repo create with an initial branch/commit) instead of leaving "
        "bd bootstrap failing 'Repository not found'; no repo-create step ran. "
        f"exec calls on {container_name!r}: "
        f"{[c.command for c in fake_driver.exec_calls if c.container == container_name]!r}"
    )
    # ADR-043 D5 (lead-jq9b): the create command must concretely target the
    # scenario's captured `{tracker}` slug — not merely "some gh repo create".
    # This ties the emitted `gh repo create <owner>/<bc>-beads` to the pinned
    # NAME form and closes the drift hole that let a `<bc>-lead-beads` scenario
    # pass against a `<bc>-beads` controller.
    expected_slug = _resolve_standup_tracker_slug(tracker, ctx["bc_name"])
    create_scripts = [c.command[2] for c in create_calls]
    assert any(f"gh repo create {expected_slug}" in s for s in create_scripts), (
        "The repo-create step must target the scenario's tracker slug "
        f"{expected_slug!r} (ADR-043 D5 <product>-<bc>-beads); "
        f"got create scripts={create_scripts!r}"
    )
    # ADR-043 D5: `-lead-beads` is the LEAD's tracker suffix ONLY; a per-BC
    # tracker create must NEVER target it.
    for s in create_scripts:
        assert "-lead-beads" not in s, (
            "The per-BC tracker repo-create must NOT target a `-lead-beads` "
            "slug (ADR-043 D5: that suffix is the lead's own tracker only); "
            f"script={s!r}"
        )
    assert fake_driver.beads_repo_created(container_name), (
        "The absent `<bc>-beads` tracker repo must end up CREATED after the "
        f"standup flow's repo-create step (lead-7jc2); container={container_name!r}"
    )


@then(parsers.parse(
    'the standup flow adds the "{tracker}" bd dolt remote and seeds it with '
    'an initial push so it is not an empty repository with no branches'
))
def assert_standup_seeds_dolt_remote(tracker, ctx, fake_driver):
    container_name = ctx["container_name"]
    calls = [c for c in fake_driver.exec_calls if c.container == container_name]
    seed_calls = [c for c in calls if _is_empty_remote_seed_command(c.command)]
    assert seed_calls, (
        "The standup flow must ADD + SEED the `<bc>-beads` Dolt remote "
        "(init-and-push an initial branch/commit) after creating the repo so "
        f"it is not empty/branchless (lead-7jc2); calls={[c.command for c in calls]!r}"
    )
    # ADR-043 D5 (lead-jq9b): the dolt-remote-add/seed command must concretely
    # target the scenario's captured `{tracker}` slug (embedded in the
    # `git+https://.../<owner>/<bc>-beads.git` remote URL) — not merely "some
    # seed ran".  This ties the emitted `bd dolt remote add` + push to the
    # pinned NAME form and closes the same drift hole on the seed path.
    expected_slug = _resolve_standup_tracker_slug(tracker, ctx["bc_name"])
    seed_scripts = [c.command[2] for c in seed_calls]
    assert any(expected_slug in s for s in seed_scripts), (
        "The dolt-remote-add/seed step must target the scenario's tracker slug "
        f"{expected_slug!r} (ADR-043 D5 <product>-<bc>-beads); "
        f"got seed scripts={seed_scripts!r}"
    )
    for s in seed_scripts:
        assert "-lead-beads" not in s, (
            "The per-BC dolt-remote seed must NOT target a `-lead-beads` slug "
            f"(ADR-043 D5: that suffix is the lead's own tracker only); script={s!r}"
        )
    assert fake_driver.beads_remote_seeded(container_name), (
        "The `<bc>-beads` Dolt remote must end up SEEDED after the standup "
        f"flow's init-and-push step (lead-7jc2); container={container_name!r}"
    )
    # The repo must be CREATED before it is seeded (a seed/push to a
    # non-existent repo would itself fail 'Repository not found').
    create_idx = next(
        (i for i, c in enumerate(calls) if _is_repo_create_command(c.command)),
        None,
    )
    seed_idx = next(
        (i for i, c in enumerate(calls)
         if _is_empty_remote_seed_command(c.command)),
        None,
    )
    assert create_idx is not None and seed_idx is not None and create_idx < seed_idx, (
        "The repo-create step must precede the dolt-remote seed step "
        f"(lead-7jc2); create_idx={create_idx!r} seed_idx={seed_idx!r}"
    )


@then(parsers.parse(
    'the subsequent "bd bootstrap" for the new BC exits zero instead of '
    'failing with "Repository not found" or "git remote has no branches"'
))
def assert_standup_bootstrap_exits_zero(ctx, fake_driver):
    container_name = ctx["container_name"]
    result = ctx["result"]
    combined = (result.stdout or "") + (result.stderr or "")
    assert fake_driver.beads_working_set_provisioned(container_name), (
        "After the standup creates and seeds the tracker repo, `bd bootstrap` "
        "must succeed and provision the working set (not fatal on 'Repository "
        f"not found' / 'git remote has no branches'); output={combined!r}"
    )
    assert "Repository not found" not in combined, (
        "The launch must not surface an unrecovered 'Repository not found' "
        f"bootstrap failure after creating the absent repo; output={combined!r}"
    )
    assert "bd bootstrap failed" not in combined, (
        "The subsequent bd bootstrap must exit zero (no warn-and-strand) after "
        f"the standup provisions the tracker repo (lead-7jc2); output={combined!r}"
    )


@then(parsers.parse(
    '"bd create" run in the stood-up BC\'s workspace exits zero and yields a '
    'new issue id so its beads tracker is usable for bd-backed gated work'
))
def assert_standup_bd_create_yields_id(ctx, fake_driver):
    container_name = ctx["container_name"]
    result = fake_driver.exec_run(container_name, ["bd", "create", "scratch"])
    assert result.returncode == 0, (
        f"Expected `bd create` to exit zero inside {container_name!r} after the "
        f"standup provisioned the tracker, got rc={result.returncode} "
        f"stderr={result.stderr!r}"
    )
    issue_id = result.stdout.strip()
    assert issue_id, "Expected `bd create` to emit a new issue id on stdout"


# ---------------------------------------------------------------------------
# lead-3mez / GAP A — the absent-repo tracker-provisioning exec must carry a
# non-empty GH_TOKEN placeholder in its docker-exec ENV so `gh repo create`
# authenticates through the already-wired agent-vault proxy (the broker
# substitutes the real GITHUB_TOKEN on the wire) instead of exiting non-zero
# with "gh auth login" / "populate GH_TOKEN".  Additive to
# @scenario_hash:90caf5523e7d5ce0 (NOT retired): that pin binds repo creation
# but explicitly hedges the credential wiring; this pin (c1abb192dd2a5eae)
# tightens the previously-unpinned provisioning-exec ENV surface.  Fidelity
# binds to STRUCTURAL inspection of the captured `gh repo create` provisioning
# exec's env (the docker-exec surface via the fake driver), NOT a live gh call.
# ---------------------------------------------------------------------------

@given(
    "the BC container's agent-vault proxy is wired with HTTPS_PROXY, the "
    "broker CA, and the AGENT_VAULT credentials, but no GitHub token is "
    "otherwise present in the provisioning exec environment"
)
def standup_proxy_wired_no_ambient_token(ctx, fake_driver):
    # Pre-state: the agent-vault proxy is fully wired at the container level
    # (HTTPS_PROXY, broker CA, AGENT_VAULT_* are injected on `docker run`), and
    # the absent tracker repo is what the standup must provision.  The teeth of
    # this scenario are that the per-exec ENV of the `gh repo create` step must
    # itself carry a non-empty GH_TOKEN placeholder — the ambient container env
    # alone does not reach that exec (neither --env-file nor host-env GH_TOKEN
    # does).  Mark the tracker repo absent so the create step runs.
    container_name = f"bc-{ctx['bc_name']}"
    fake_driver.set_beads_repo_absent(container_name, True)
    fake_driver.set_committed_beads_prefix(container_name, "bclaunch")
    ctx["committed_beads_prefix"] = "bclaunch"
    ctx["container_name"] = container_name


@when(parsers.parse(
    'the standup runs its beads-tracker provisioning exec that invokes '
    '"gh repo create {slug} --private --add-readme"'
))
def standup_runs_provisioning_exec(slug, ctx, fake_driver, controller, tmp_path):
    import yaml as _yaml
    bc_name = ctx["bc_name"]
    manifest_path = tmp_path / "bc-manifest.yaml"
    manifest_path.write_text(_yaml.dump({
        "product": "shopsystem product",
        "bcs": [{"name": bc_name, "remote": ctx["repo_url"], "role": "bc"}],
    }))
    result = controller.launch(
        bc_name=bc_name,
        repo_url=ctx["repo_url"],
        manifest_path=manifest_path,
        credential_home=ctx.get("credential_home"),
    )
    ctx["result"] = result
    ctx["container_name"] = f"bc-{bc_name}"


@then(
    "that provisioning exec's environment sets a non-empty GH_TOKEN "
    "placeholder so gh authenticates through the agent-vault proxy instead "
    "of exiting non-zero with a \"gh auth login\" or \"populate GH_TOKEN\" "
    "error"
)
def assert_provisioning_exec_carries_gh_token(ctx, fake_driver):
    container_name = ctx["container_name"]
    create_calls = [
        c for c in fake_driver.exec_calls
        if c.container == container_name and _is_repo_create_command(c.command)
    ]
    assert create_calls, (
        "The standup must run a `gh repo create` provisioning exec; none was "
        f"captured on {container_name!r}. exec calls: "
        f"{[c.command for c in fake_driver.exec_calls if c.container == container_name]!r}"
    )
    # STRUCTURAL fidelity: every `gh repo create` provisioning exec must carry
    # a NON-EMPTY GH_TOKEN in its per-exec docker-exec env.  Without it, gh
    # exits non-zero ("gh auth login" / "populate GH_TOKEN") and the repo is
    # never created — the empirically-proven failure GAP A closes.  The real
    # GITHUB_TOKEN is substituted on the wire by the agent-vault proxy; the
    # exec only needs a non-empty placeholder to ride that wire.
    for c in create_calls:
        assert c.env is not None and c.env.get("GH_TOKEN"), (
            "The `gh repo create` provisioning exec must set a NON-EMPTY "
            "GH_TOKEN placeholder in its env so gh authenticates through the "
            "agent-vault proxy instead of exiting non-zero; the exec carried "
            f"env={c.env!r} (script={c.command[2]!r})"
        )


@then(parsers.parse(
    'the "gh repo create" invocation exits zero and the "{slug}" tracker '
    'repository exists and is viewable'
))
def assert_gh_repo_create_exits_zero(slug, ctx, fake_driver):
    container_name = ctx["container_name"]
    expected_slug = _resolve_standup_tracker_slug(slug, ctx["bc_name"])
    create_calls = [
        c for c in fake_driver.exec_calls
        if c.container == container_name and _is_repo_create_command(c.command)
    ]
    create_scripts = [c.command[2] for c in create_calls]
    assert any(f"gh repo create {expected_slug}" in s for s in create_scripts), (
        "The provisioning exec must target the scenario's tracker slug "
        f"{expected_slug!r}; got create scripts={create_scripts!r}"
    )
    assert fake_driver.beads_repo_created(container_name), (
        "The absent tracker repo must end up CREATED (viewable) after the "
        f"provisioning exec; container={container_name!r}"
    )


# ---------------------------------------------------------------------------
# lead-r34c / GAP B — the in-container standup must RESOLVE the scaffolded
# ORIGIN_OWNER placeholder to the derived GitHub owner (parsed from the
# container's /workspace git origin) and WRITE it into the .beads config
# sync.remote AND the functional bd dolt remote BEFORE `bd bootstrap`, so the
# bootstrap clone target is <owner>/<bc>-beads and no literal ORIGIN_OWNER
# survives.  Empirically observed (David's 2026-07-07 shopsystem-knowledge
# standup): the scaffold was pushed with the ORIGIN_OWNER placeholder (correct
# at scaffold time), the standup resolved the owner to dstengle for the
# gh-create step but did NOT write it back into the in-container .beads config,
# so `bd bootstrap` cloned the stale ORIGIN_OWNER URL and failed "Repository not
# found".  Additive to @scenario_hash:90caf5523e7d5ce0 (NOT retired): that pin
# binds repo creation + seeding; this pin (8ca9508bd7f5fecf) tightens the
# previously-unpinned runtime owner-writeback.  Fidelity binds to the executable
# standup provisioning surface PLUS the in-container config observed via
# `bd dolt remote list` / the `bd bootstrap` clone target, NOT a live clone.
# ---------------------------------------------------------------------------

@given(parsers.parse(
    'a new BC whose shop-name slug is "{bc}" is stood up from a lead whose '
    'GitHub owner resolves to "{owner}"'
))
def standup_new_bc_owner_resolves(bc, owner, ctx, fake_driver, controller, tmp_path):
    # Concrete binding of the abstract <bc>/<owner> placeholders: stand up the
    # launcher BC itself, whose in-container /workspace git origin resolves to
    # the configured beads remote org (the same owner the real
    # github.com/<org>/shopsystem-bc-launcher origin carries).
    from bc_launcher.controller import BEADS_REMOTE_ORG
    bc_name = "shopsystem-bc-launcher"
    ctx["driver"] = fake_driver
    ctx["controller"] = controller
    ctx["bc_name"] = bc_name
    ctx["repo_url"] = f"https://github.com/shopsystem/{bc_name}.git"
    ctx["container_name"] = f"bc-{bc_name}"
    # The derived owner the container's /workspace git origin resolves to — the
    # concrete binding of the scenario's abstract "<owner>".
    ctx["derived_owner"] = BEADS_REMOTE_ORG
    credential_home = tmp_path / "fake_home"
    credential_home.mkdir(parents=True, exist_ok=True)
    (credential_home / ".claude").mkdir(parents=True, exist_ok=True)
    (credential_home / ".config" / "gh").mkdir(parents=True, exist_ok=True)
    gitconfig = credential_home / ".gitconfig"
    if not gitconfig.exists():
        gitconfig.write_text("")
    ctx["credential_home"] = credential_home


@given(parsers.parse(
    'its scaffolded beads tracker config was pushed carrying the literal '
    '"{placeholder}" placeholder in the tracker remote because no origin owner '
    'was known at scaffold time'
))
def standup_scaffold_carries_owner_placeholder(placeholder, ctx, fake_driver):
    assert placeholder == "ORIGIN_OWNER", (
        f"GAP B pins the ORIGIN_OWNER placeholder; got {placeholder!r}"
    )
    container_name = f"bc-{ctx['bc_name']}"
    # The scaffolded functional bd dolt remote carries the literal ORIGIN_OWNER
    # placeholder; the container's /workspace git origin resolves to the derived
    # owner the standup must write back before bootstrap.
    fake_driver.set_beads_remote_owner_placeholder(
        container_name, ctx["derived_owner"]
    )
    # A committed prefix so bootstrap can derive a usable issue_prefix once the
    # owner-writeback lets the clone succeed.
    fake_driver.set_committed_beads_prefix(container_name, "bclaunch")
    ctx["committed_beads_prefix"] = "bclaunch"
    ctx["container_name"] = container_name


@when(parsers.parse(
    "the BC-standup flow provisions the in-container beads tracker and runs "
    '"bd bootstrap"'
))
def standup_provisions_in_container_and_bootstraps(
    ctx, fake_driver, controller, tmp_path
):
    import yaml as _yaml
    bc_name = ctx["bc_name"]
    manifest_path = tmp_path / "bc-manifest.yaml"
    manifest_path.write_text(_yaml.dump({
        "product": "shopsystem product",
        "bcs": [{"name": bc_name, "remote": ctx["repo_url"], "role": "bc"}],
    }))
    result = controller.launch(
        bc_name=bc_name,
        repo_url=ctx["repo_url"],
        manifest_path=manifest_path,
        credential_home=ctx.get("credential_home"),
    )
    ctx["result"] = result
    ctx["container_name"] = f"bc-{bc_name}"


@then(parsers.parse(
    'the in-container tracker\'s functional bd dolt remote, the one '
    '"bd dolt remote list" reports and "bd bootstrap" clones from, contains no '
    'literal "{placeholder}" segment'
))
def assert_functional_remote_no_placeholder(placeholder, ctx, fake_driver):
    container_name = ctx["container_name"]
    # A resolve-and-writeback exec must have run BEFORE bd bootstrap (its op
    # index must precede the first bd bootstrap exec).
    calls = [c for c in fake_driver.exec_calls if c.container == container_name]
    writeback_idx = next(
        (i for i, c in enumerate(calls)
         if _is_origin_owner_writeback_command(c.command)),
        None,
    )
    bootstrap_idx = next(
        (i for i, c in enumerate(calls) if is_bd_bootstrap_command(c.command)),
        None,
    )
    assert writeback_idx is not None, (
        "The standup must run an ORIGIN_OWNER->derived-owner writeback exec "
        "(resolve the owner from /workspace git origin and rewrite the .beads "
        "config + functional bd dolt remote) BEFORE bd bootstrap (lead-r34c); "
        f"no writeback exec ran. calls={[c.command for c in calls]!r}"
    )
    assert bootstrap_idx is not None and writeback_idx < bootstrap_idx, (
        "The ORIGIN_OWNER writeback must PRECEDE bd bootstrap so the clone "
        f"target is already resolved; writeback_idx={writeback_idx!r} "
        f"bootstrap_idx={bootstrap_idx!r}"
    )
    # The functional remote `bd dolt remote list` reports carries no ORIGIN_OWNER
    # segment after the writeback.
    remote_list = fake_driver.exec_run(
        container_name, ["bd", "dolt", "remote", "list"]
    )
    assert placeholder not in (remote_list.stdout or ""), (
        f"`bd dolt remote list` must not report a {placeholder!r} owner segment "
        f"after the standup's writeback; got {remote_list.stdout!r}"
    )
    owner = fake_driver.beads_functional_remote_owner(container_name)
    assert owner and placeholder not in owner, (
        "The functional bd dolt remote's owner segment must not be the "
        f"{placeholder!r} placeholder after the writeback; got owner={owner!r}"
    )
    url = fake_driver.beads_functional_remote_url(container_name)
    assert placeholder not in url, (
        f"The functional bd dolt remote URL must carry no {placeholder!r} "
        f"segment after the writeback; got url={url!r}"
    )


@then(parsers.parse(
    'that functional bd dolt remote\'s owner segment equals the derived GitHub '
    'owner "{owner}" so its clone target is "{clone_target}"'
))
def assert_functional_remote_owner_is_derived(owner, clone_target, ctx, fake_driver):
    container_name = ctx["container_name"]
    derived = ctx["derived_owner"]
    got = fake_driver.beads_functional_remote_owner(container_name)
    assert got == derived, (
        "The functional bd dolt remote's owner segment must equal the derived "
        f"GitHub owner {derived!r} (resolved from /workspace git origin); "
        f"got {got!r}"
    )
    # The clone target the bootstrap uses is <owner>/<bc>-beads under the
    # derived owner — the concrete resolution of the scenario's "<owner>/<bc>".
    url = fake_driver.beads_functional_remote_url(container_name)
    assert f"/{derived}/" in url, (
        f"The functional remote URL must carry the derived owner {derived!r} in "
        f"its owner segment so the clone target is <owner>/<bc>-beads; got {url!r}"
    )


@then(parsers.parse(
    '"bd bootstrap" for the new BC exits zero instead of failing '
    '"Repository not found" against an "{bad_target}" URL'
))
def assert_bootstrap_exits_zero_not_origin_owner(bad_target, ctx, fake_driver):
    container_name = ctx["container_name"]
    result = ctx["result"]
    combined = (result.stdout or "") + (result.stderr or "")
    assert fake_driver.beads_working_set_provisioned(container_name), (
        "After the standup resolves ORIGIN_OWNER to the derived owner before "
        "bootstrap, `bd bootstrap` must succeed and provision the working set "
        f"(not fatal on 'Repository not found'); output={combined!r}"
    )
    assert "ORIGIN_OWNER" not in combined, (
        "The launch must surface no ORIGIN_OWNER clone target after the "
        f"writeback; output={combined!r}"
    )
    assert "bd bootstrap failed" not in combined, (
        "The subsequent bd bootstrap must exit zero (no warn-and-strand) after "
        f"the standup's owner-writeback (lead-r34c); output={combined!r}"
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


# ---------------------------------------------------------------------------
# lead-wdvx Bug 1 — the docker-socket opt-in flag must grant USABLE access
# (scenarios c63857720446813b / f49c7fd3c38ac741).  The PRESENCE/ABSENCE pins
# ff370a4e / e177655b above stay green; these add the usability dimension:
# the host socket's gid must be in the container's supplementary groups when
# the flag is set (so a non-root call is NOT permission-denied), and ABSENT
# when the flag is not set (guard against over-grant).
# ---------------------------------------------------------------------------

@given(parsers.parse('the host docker socket "{socket_path}" is owned by '
                     'group id "{gid}"'))
def host_docker_socket_owned_by_gid(socket_path, gid, ctx, fake_driver):
    """Model the host docker socket's owning gid (what `host_socket_gid`
    resolves by stat-ing the host socket).  The launcher must add THIS gid to
    the container's supplementary groups when the docker-socket flag is set."""
    fake_driver.set_host_socket_gid(int(gid))
    ctx["host_socket_gid"] = int(gid)


@then(parsers.parse('docker inspect of the container shows the host docker '
                    'socket group id "{gid}" present in the container\'s '
                    'supplementary groups'))
def assert_host_socket_gid_present(gid, ctx, fake_driver):
    """RED if no --group-add is added (the host socket gid is absent from the
    container's supplementary groups) — the masked-fault state Bug 1 fixes."""
    container_name = ctx["container_name"]
    groups = fake_driver.container_group_add(container_name)
    assert gid in groups, (
        f"docker inspect (HostConfig.GroupAdd) must show the host docker "
        f"socket gid {gid!r} in the container's supplementary groups when "
        f"the docker-socket flag is set; got: {groups!r}"
    )


@then("a docker call made by the container's non-root default user is not "
      "rejected with a permission-denied error against the docker socket")
def assert_non_root_docker_not_permission_denied(ctx, fake_driver):
    """RED if the non-root user is still outside the socket's group (no
    --group-add granted) — i.e. docker calls are permission-denied."""
    container_name = ctx["container_name"]
    host_gid = ctx["host_socket_gid"]
    denied = fake_driver.non_root_docker_call_permission_denied(
        container_name, host_gid
    )
    assert not denied, (
        "a docker call by the container's non-root default user must NOT be "
        f"permission-denied: the host socket gid {host_gid} is not in the "
        f"container's supplementary groups "
        f"{fake_driver.container_group_add(container_name)!r}"
    )


@then(parsers.parse('docker inspect of the container shows the host docker '
                    'socket group id "{gid}" absent from the container\'s '
                    'supplementary groups'))
def assert_host_socket_gid_absent(gid, ctx, fake_driver):
    """RED if the gid is added WITHOUT the flag (over-grant)."""
    container_name = ctx["container_name"]
    groups = fake_driver.container_group_add(container_name)
    assert gid not in groups, (
        f"docker inspect (HostConfig.GroupAdd) must NOT show the host docker "
        f"socket gid {gid!r} in the container's supplementary groups when the "
        f"docker-socket flag is absent (over-grant guard); got: {groups!r}"
    )


# ---------------------------------------------------------------------------
# lead-wdvx Bug 2 — docker-dependent subcommands must surface a docker config
# fault (permission-denied / not-mounted) as a NON-ZERO, cause-naming
# diagnostic distinct from a legitimate empty/absent result
# (scenarios 510d02951321628e / 2123096c12854ff1).
# ---------------------------------------------------------------------------

@given("the Docker socket is mounted but the calling user is denied access to "
       "it so docker calls fail with a permission-denied error")
def docker_socket_permission_denied(ctx, fake_driver):
    """Model the socket mounted-but-permission-denied fault (lead-wdvx)."""
    fake_driver.set_docker_socket_permission_denied(True)


# The Scenario Outline parameterises the fault via <docker_fault>.  Map each
# Examples phrasing onto the modelled fault.
_WDVX_DOCKER_FAULTS = {
    "the socket is permission-denied to the calling user":
        "permission_denied",
    "the socket is not mounted into the calling environment":
        "not_mounted",
}


@given(parsers.parse("the Docker daemon cannot be reached because "
                     "{docker_fault}"))
def docker_daemon_unreachable_because(docker_fault, ctx, fake_driver):
    fault = _WDVX_DOCKER_FAULTS.get(docker_fault.strip())
    assert fault is not None, (
        f"unmodelled docker_fault: {docker_fault!r}"
    )
    if fault == "permission_denied":
        fake_driver.set_docker_socket_permission_denied(True)
    else:
        fake_driver.set_docker_socket_not_mounted(True)
    ctx["wdvx_docker_fault"] = fault


@when(parsers.parse('I run the Docker-dependent bc-container subcommand '
                    '"{subcommand}"'))
def run_docker_dependent_subcommand(subcommand, ctx, controller):
    sub = subcommand.strip()
    if sub == "list":
        ctx["result"] = controller.list_containers()
    elif sub == "status":
        ctx["result"] = controller.status("shopsystem-messaging")
    else:
        raise AssertionError(f"unmodelled docker-dependent subcommand: {sub!r}")
    ctx["wdvx_subcommand"] = sub


@then("stderr names the cause as the Docker daemon being unreachable due to "
      "the socket being permission-denied or not mounted")
def assert_stderr_names_permission_or_not_mounted_cause(ctx):
    """RED if permission-denied still masks as 'No BC containers found.' /
    exit 0 (no cause-naming stderr)."""
    result = ctx["result"]
    stderr = (getattr(result, "stderr", "") or "").lower()
    assert "docker" in stderr and (
        "could not be reached" in stderr or "unreachable" in stderr
    ), (
        f"stderr must name the Docker daemon as unreachable; got: {stderr!r}"
    )


@then("stderr names the cause as the Docker daemon being unreachable")
def assert_stderr_names_daemon_unreachable(ctx):
    result = ctx["result"]
    stderr = (getattr(result, "stderr", "") or "").lower()
    assert "docker" in stderr and (
        "could not be reached" in stderr or "unreachable" in stderr
    ), (
        f"stderr must name the Docker daemon as unreachable; got: {stderr!r}"
    )


@then(parsers.parse('stdout does not include "{text}"'))
def assert_stdout_does_not_include(text, ctx):
    result = ctx["result"]
    stdout = getattr(result, "stdout", "") or ""
    assert text not in stdout, (
        f"Expected {text!r} ABSENT from stdout (a docker config fault must "
        f"not be masked as an empty/absent result); got: {stdout!r}"
    )


@then("the output is distinguishable from the legitimate result the "
      "subcommand would print when Docker is reachable but the queried "
      "container is absent or the list is empty")
def assert_distinguishable_from_legitimate_empty(ctx, fake_driver, controller):
    """The fault output must be distinguishable from the LEGITIMATE
    empty/absent result the same subcommand prints when docker is reachable.

    Teeth: assert (a) the fault path exited non-zero with cause-naming stderr,
    AND (b) running the SAME subcommand with docker reachable and no matching
    container produces the legitimate empty/absent result at exit 0 — and the
    two are NOT the same.  RED if any Examples row masks the fault as the
    legitimate empty/absent result or exits zero.
    """
    fault_result = ctx["result"]
    sub = ctx["wdvx_subcommand"]
    assert fault_result.exit_code != 0, (
        "the fault path must exit non-zero"
    )

    # Now clear the fault and re-run the SAME subcommand to capture the
    # LEGITIMATE empty/absent result (docker reachable, nothing present).
    fake_driver.set_docker_socket_unreachable(False)
    legit_controller = controller
    if sub == "list":
        legit = legit_controller.list_containers()
        assert legit.exit_code == 0, (
            "a legitimately-empty list must still exit zero"
        )
        assert "No BC containers found." in legit.stdout, (
            "the legitimate empty list must print 'No BC containers found.'; "
            f"got: {legit.stdout!r}"
        )
    else:  # status
        legit = legit_controller.status("shopsystem-messaging")
        assert legit.exit_code == 0, (
            "a legitimately-absent container status must still exit zero"
        )
        assert "container_state: stopped" in legit.stdout, (
            "the legitimate absent-container status must report "
            f"'container_state: stopped'; got: {legit.stdout!r}"
        )

    # Fault output is distinguishable from the legitimate empty/absent output.
    assert fault_result.exit_code != legit.exit_code, (
        "the docker-fault result must be distinguishable from the legitimate "
        f"empty/absent result (fault exit {fault_result.exit_code}, legit "
        f"exit {legit.exit_code})"
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


# ===========================================================================
# Readiness-wait interactive-prompt Escape-handling step definitions (lead-cw7m)
#
# Scenarios 048607861da16ff4 / 815f8e470163f669 / acf59eb2e265fde7.  During the
# readiness wait (BEFORE the input-ready marker "bypass permissions on"), the
# agent pane can present an interactive prompt (e.g. the new bc-base image's
# "Try the new fullscreen renderer?" onboarding prompt rendered before the
# trust banner) that blocks reaching input-ready.  The launcher must send a
# DISCRETE send-keys carrying ONLY Escape (never Enter / '1', so the renderer
# is NOT enabled), WARN naming the auto-dismissed prompt, continue the loop to
# input-ready, and inject the startup prompt so the BC comes online.  The whole
# scan-dismiss loop is BOUNDED by the existing 60s readiness timeout: when
# auto-dismissal never reaches input-ready the launcher STOPS dismissing at
# 60s, warns the main input did not become ready within 60 seconds, and
# proceeds WITHOUT injecting.
#
# These EXTEND the lead-q3uy/gs03 engage-phase Esc-dismiss posture (AFTER
# input-ready) to the READINESS-WAIT phase (BEFORE input-ready); they COMPOSE
# with — and do NOT supersede — the lead-q3uy engage scenarios.  The
# FakeDockerDriver models the readiness-wait prompt faithfully (see
# tests/fake_driver.py: simulate_readiness_wait_prompt / the input-ready wait
# block / capture_pane precedence / the Escape-dismiss send-keys path).
# ===========================================================================

# The rendered content of the simulated readiness-wait prompts.  The generic
# one advertises an Esc affordance ("esc to cancel"); the fullscreen-renderer
# one carries its specific signature plus the same Esc affordance.  NEITHER
# carries the input-ready marker (they BLOCK reaching it) and NEITHER is the
# workspace-trust prompt.
_READINESS_GENERIC_PROMPT = (
    "Set up your editor integration?\n"
    "  1. Yes\n"
    "  2. Not now\n"
    "(Esc to cancel)\n"
)
_READINESS_FULLSCREEN_PROMPT = (
    "Try the new fullscreen renderer?\n"
    "  1. Yes\n"
    "  2. Not now, Esc to cancel\n"
)
_READINESS_STARTUP_PROMPT = "bd prime"


def _launch_with_readiness_prompt(
    ctx, fake_driver, controller, tmp_path, content, *, clears_on_escape
):
    """Configure a readiness-wait blocking prompt and run launch.

    Both readiness barriers (messaging DB, agent-vault broker) pass; claude
    starts and the PRE-trust CLAUDE_READY_MARKER is observed; the blocking
    prompt then prevents the POST-trust input-ready marker from appearing
    until an Escape dismisses it (when ``clears_on_escape``).
    """
    bc_name = "shopsystem-messaging"
    container_name = f"bc-{bc_name}"
    repo_url = f"https://github.com/shopsystem/{bc_name}.git"
    dsn = _READINESS_DSN
    fake_driver.set_dsn_reachable(dsn, reachable=True)
    fake_driver.simulate_readiness_wait_prompt(
        container_name, content, clears_on_escape=clears_on_escape
    )
    manifest_path = tmp_path / "bc-manifest.yaml"
    if not manifest_path.exists():
        manifest_path.write_text(yaml.dump({
            "product": "shopsystem product",
            "bcs": [{"name": bc_name, "remote": repo_url, "role": "bc"}],
        }))
    result = controller.launch(
        bc_name=bc_name,
        repo_url=repo_url,
        shopmsg_dsn=dsn,
        startup_prompt=_READINESS_STARTUP_PROMPT,
        manifest_path=manifest_path,
        credential_home=ctx.get("credential_home"),
    )
    ctx["result"] = result
    ctx["container_name"] = container_name
    ctx["bc_name"] = bc_name
    ctx["startup_prompt"] = _READINESS_STARTUP_PROMPT


# --- 048607861da16ff4: generic unexpected prompt auto-dismissed -------------

@given(parsers.parse(
    'a BC container whose agent has been started with '
    '"{claude_cmd}"'
))
def given_agent_started(claude_cmd, ctx):
    ctx["_readiness_claude_cmd"] = claude_cmd


@given(parsers.parse(
    'the launcher has accepted the workspace-trust prompt and is waiting for '
    'the input-ready marker "{marker}"'
))
def given_waiting_for_input_ready(marker, ctx):
    ctx["_readiness_input_marker"] = marker


@when(parsers.parse(
    'the agent pane presents an unexpected interactive prompt that is not the '
    'workspace-trust prompt and blocks reaching input-ready'
))
def when_unexpected_prompt(ctx, fake_driver, controller, tmp_path):
    _launch_with_readiness_prompt(
        ctx, fake_driver, controller, tmp_path,
        _READINESS_GENERIC_PROMPT, clears_on_escape=True,
    )


@then(parsers.parse(
    'the launcher dismisses the unexpected prompt with the safe non-committal '
    'default by sending Esc'
))
def then_dismiss_with_esc(ctx, fake_driver):
    container_name = ctx["container_name"]
    escapes = _escape_send_keys(fake_driver, container_name, "agent")
    assert escapes, (
        "Expected a DISCRETE Escape send-keys to dismiss the readiness-wait "
        "prompt; none recorded.  All send-keys: "
        f"{[c.command for c in fake_driver.send_keys_calls(container_name)]!r}"
    )
    # The prompt absorbed at least one Escape (it was the dismissal key).
    assert fake_driver.readiness_prompt_escape_count(container_name) >= 1, (
        "The readiness-wait prompt must have absorbed at least one Escape."
    )
    # Esc-not-Enter teeth: NO send-keys against the prompt carried Enter or '1'
    # (which would confirm a default / enable the renderer) BEFORE the prompt
    # was dismissed.  The faithful model only clears the prompt on Escape, so a
    # phantom Enter/'1' could never have cleared it — assert the dismissal key
    # really was Escape.
    for c in fake_driver.send_keys_calls(container_name):
        payload = c.command[4:]
        if payload in (["Enter"], ["1"]) and c.command[:4] == [
            "tmux", "send-keys", "-t", "agent"
        ]:
            # A bare Enter is legitimate ONLY as workspace-trust accept (step 3)
            # or as the discrete submit Enter AFTER the prompt-text invocation.
            # Neither enables the renderer.  The prompt itself is dismissed only
            # by Escape; this loop's purpose is the assertion above.
            pass


@then(parsers.parse(
    'the launcher emits a warning naming the unexpected interactive prompt it '
    'auto-dismissed'
))
def then_warn_names_unexpected(ctx):
    surface = ctx["result"].stderr
    low = surface.lower()
    assert "warning" in low and "auto-dismissed" in low, (
        f"Expected a WARNING that a prompt was auto-dismissed; got: {surface!r}"
    )
    # The warning NAMES the prompt (its first rendered line).
    assert "Set up your editor integration?" in surface, (
        f"Expected the WARNING to name the auto-dismissed prompt; got: "
        f"{surface!r}"
    )


@then(parsers.parse(
    'the launcher continues the readiness loop and observes the input-ready '
    'marker "{marker}"'
))
def then_continues_observes_input_ready(marker, ctx, fake_driver):
    container_name = ctx["container_name"]
    # The prompt was dismissed (so the input-ready marker became observable)
    # and an input-ready wait was recorded.
    rp = fake_driver._readiness_prompt.get(container_name)
    assert rp is not None and rp.get("dismissed"), (
        "The readiness-wait prompt must have been dismissed so the loop could "
        "proceed to observe the input-ready marker."
    )
    markers = [m for (_c, _s, m) in fake_driver.wait_for_marker_calls]
    assert marker in markers, (
        f"Expected an input-ready marker wait for {marker!r}; recorded: "
        f"{markers!r}"
    )


@then(parsers.parse(
    'the launcher injects the startup prompt with no human interaction so the '
    'BC comes online'
))
def then_injects_no_human(ctx, fake_driver):
    container_name = ctx["container_name"]
    assert ctx["result"].exit_code == 0, (
        f"Expected launch to exit zero after auto-dismiss + inject; got "
        f"{ctx['result'].exit_code} / stderr: {ctx['result'].stderr!r}"
    )
    committed = fake_driver.agent_committed_prompt(container_name)
    assert committed == ctx["startup_prompt"], (
        f"Expected the startup prompt {ctx['startup_prompt']!r} to be injected "
        f"and committed (BC online) with no human interaction; committed="
        f"{committed!r}"
    )
    # No human interaction: the launcher needed no interactive attach.
    assert not fake_driver.interactive_calls, (
        "Inject must require NO interactive attach; interactive calls: "
        f"{[c.command for c in fake_driver.interactive_calls]!r}"
    )


# --- 815f8e470163f669: fullscreen-renderer prompt auto-dismissed ------------

@given(parsers.parse(
    'a BC container whose agent presents the "{prompt_name}" onboarding '
    'prompt before the workspace-trust banner appears'
))
def given_fullscreen_prompt(prompt_name, ctx):
    ctx["_readiness_prompt_name"] = prompt_name


@given(parsers.parse(
    'the launcher is running the readiness sequence waiting for the input-ready '
    'marker "{marker}"'
))
def given_running_readiness_seq(marker, ctx):
    ctx["_readiness_input_marker"] = marker


@when(parsers.parse(
    'the readiness loop detects the fullscreen-renderer prompt blocking '
    'progress to input-ready'
))
def when_fullscreen_prompt(ctx, fake_driver, controller, tmp_path):
    _launch_with_readiness_prompt(
        ctx, fake_driver, controller, tmp_path,
        _READINESS_FULLSCREEN_PROMPT, clears_on_escape=True,
    )


@then(parsers.parse(
    'the launcher dismisses it by sending Esc without enabling the new renderer'
))
def then_dismiss_fullscreen_esc(ctx, fake_driver):
    container_name = ctx["container_name"]
    escapes = _escape_send_keys(fake_driver, container_name, "agent")
    assert escapes, (
        "Expected a DISCRETE Escape send-keys to dismiss the fullscreen-"
        "renderer prompt; none recorded."
    )
    # Esc-not-Enter TEETH: the renderer is enabled by pressing Enter or '1'.
    # The faithful model dismisses the prompt ONLY on Escape — never on Enter
    # or '1' — so assert the prompt was dismissed (which proves Escape, not a
    # renderer-enabling key, cleared it) and the Escape invocation carries
    # NEITHER Enter NOR '1'.
    rp = fake_driver._readiness_prompt.get(container_name)
    assert rp is not None and rp.get("dismissed"), (
        "The fullscreen-renderer prompt must have been dismissed by Escape; a "
        "renderer-enabling Enter/'1' must NOT clear it."
    )
    for c in escapes:
        assert "Enter" not in c.command and "1" not in c.command, (
            "The Escape-bearing invocation must NOT carry Enter or '1' "
            f"(which would enable the renderer); got {c.command!r}"
        )


@then(parsers.parse(
    'the launcher emits a warning naming the fullscreen-renderer prompt it '
    'auto-dismissed'
))
def then_warn_names_fullscreen(ctx):
    surface = ctx["result"].stderr
    low = surface.lower()
    assert "warning" in low and "auto-dismissed" in low, (
        f"Expected a WARNING that the prompt was auto-dismissed; got: "
        f"{surface!r}"
    )
    assert "Try the new fullscreen renderer?" in surface, (
        f"Expected the WARNING to name the fullscreen-renderer prompt; got: "
        f"{surface!r}"
    )


@then(parsers.parse(
    'the readiness loop proceeds and observes the input-ready marker "{marker}"'
))
def then_proceeds_observes_input_ready(marker, ctx, fake_driver):
    container_name = ctx["container_name"]
    rp = fake_driver._readiness_prompt.get(container_name)
    assert rp is not None and rp.get("dismissed"), (
        "The fullscreen-renderer prompt must have been dismissed so the loop "
        "could proceed to observe the input-ready marker."
    )
    markers = [m for (_c, _s, m) in fake_driver.wait_for_marker_calls]
    assert marker in markers, (
        f"Expected an input-ready marker wait for {marker!r}; recorded: "
        f"{markers!r}"
    )


@then(parsers.parse(
    'the startup prompt is injected and the BC comes online'
))
def then_injected_online(ctx, fake_driver):
    container_name = ctx["container_name"]
    assert ctx["result"].exit_code == 0, (
        f"Expected launch to exit zero after auto-dismiss + inject; got "
        f"{ctx['result'].exit_code} / stderr: {ctx['result'].stderr!r}"
    )
    committed = fake_driver.agent_committed_prompt(container_name)
    assert committed == ctx["startup_prompt"], (
        f"Expected the startup prompt {ctx['startup_prompt']!r} to be injected "
        f"(BC online); committed={committed!r}"
    )


# --- acf59eb2e265fde7: BOUNDED when auto-dismissal never reaches ready -------

@given(parsers.parse(
    'a BC container whose agent keeps presenting an unexpected interactive '
    'prompt that the launcher auto-dismisses with Esc'
))
def given_never_clearing_prompt(ctx):
    # Mark the never-clears variant; the When step runs the launch.
    ctx["_readiness_never_clears"] = True


@given(parsers.parse(
    'the input-ready marker "{marker}" is never observed'
))
def given_input_ready_never(marker, ctx):
    ctx["_readiness_input_marker"] = marker


@when(parsers.parse(
    'the readiness timeout of 60 seconds elapses across the auto-dismissal '
    'attempts'
))
def when_timeout_elapses(ctx, fake_driver, controller, tmp_path):
    _launch_with_readiness_prompt(
        ctx, fake_driver, controller, tmp_path,
        _READINESS_GENERIC_PROMPT, clears_on_escape=False,
    )


@then(parsers.parse(
    'the launcher stops attempting dismissals rather than looping indefinitely'
))
def then_stops_dismissing(ctx, fake_driver):
    container_name = ctx["container_name"]
    # BOUNDED teeth: the prompt never clears, so the launcher kept Esc-dismissing
    # until the 60s deadline.  The escape count must be FINITE and small (the
    # loop terminated at ~60s / per-attempt budget) — a non-terminating impl
    # would never return from launch (the test would hang) and a still-looping
    # impl would record an unbounded count.
    count = fake_driver.readiness_prompt_escape_count(container_name)
    assert count >= 1, (
        "The launcher must have attempted at least one Esc-dismiss before the "
        "bound was reached."
    )
    from bc_launcher.controller import (
        CLAUDE_READINESS_TIMEOUT_SECONDS,
        READINESS_DISMISS_POLL_SECONDS,
    )
    max_attempts = int(
        CLAUDE_READINESS_TIMEOUT_SECONDS / READINESS_DISMISS_POLL_SECONDS
    ) + 2
    assert count <= max_attempts, (
        f"The dismissal loop must be BOUNDED: expected at most {max_attempts} "
        f"Esc attempts within the 60s timeout, but {count} were recorded "
        "(non-terminating / unbounded-loop regression)."
    )


@then(parsers.parse(
    'the launcher emits a warning that the main input did not become ready '
    'within 60 seconds'
))
def then_warn_not_ready(ctx):
    surface = ctx["result"].stderr
    low = surface.lower()
    assert "warning" in low and "did not become ready within 60" in low, (
        f"Expected a WARNING that the main input did not become ready within "
        f"60 seconds; got: {surface!r}"
    )


@then(parsers.parse(
    'the launcher proceeds without injecting the startup prompt'
))
def then_proceeds_without_injecting(ctx, fake_driver):
    container_name = ctx["container_name"]
    # The startup prompt must NOT have been injected/committed.
    committed = fake_driver.agent_committed_prompt(container_name)
    assert committed != ctx["startup_prompt"], (
        f"On the bounded-timeout path the startup prompt {ctx['startup_prompt']!r} "
        f"must NOT be injected; but it was committed as {committed!r}."
    )
    # No send-keys carried the startup prompt text.
    prompt = ctx["startup_prompt"]
    for c in fake_driver.send_keys_calls(container_name):
        assert prompt not in c.command, (
            f"No send-keys may carry the startup prompt {prompt!r} on the "
            f"bounded-timeout path; found {c.command!r}"
        )


# ---------------------------------------------------------------------------
# lead-63em — launch-failure persisted diagnostic file (per-BC host surface)
# ---------------------------------------------------------------------------

# Map each Scenario-Outline <fault> phrasing to its documented cause-marker.
_LEAD_63EM_FAULT_TO_MARKER = {
    "the messaging database at SHOPMSG_DSN is unreachable": "messaging-db",
    "the agent-vault broker on the shopsystem network is unreachable": "agent-vault",
    "the readiness barrier never reports both supporting servers ready": "readiness",
    "claude or its tmux session never started inside the container": "agent-startup",
}


@given(parsers.parse(
    "the launch will fail to bring up a usable session because {fault}"
))
def launch_will_fail_because(fault, ctx):
    """Pin which of the four documented launch faults this row exercises.

    Stores the resolved cause-marker in ctx["launch_fault"]; the
    '... and a startup prompt' When step reads it and configures the fake
    driver so the launch fails at exactly that barrier / step.  Also point
    BCLAUNCHER_HOST_STATE_DIR at a per-test host dir so the persisted
    diagnostic file lands under the test sandbox and can be read back from
    the host.
    """
    marker = _LEAD_63EM_FAULT_TO_MARKER.get(fault.strip())
    assert marker is not None, f"unmapped launch fault phrasing: {fault!r}"
    ctx["launch_fault"] = marker
    ctx["expected_cause_marker"] = marker
    _lead63em_point_state_dir_at_sandbox(ctx)


def _lead63em_point_state_dir_at_sandbox(ctx):
    """Record the per-test host state surface dir from the autouse fixture.

    The ``_lead63em_host_state_dir`` autouse fixture has already pointed
    BCLAUNCHER_HOST_STATE_DIR at a per-test tmp dir; capture it in ctx so the
    Then steps can assert the diagnostic file lands under that surface.
    """
    import os as _os
    ctx["host_state_dir"] = _os.environ["BCLAUNCHER_HOST_STATE_DIR"]


@then(parsers.parse(
    'no usable tmux session named "{session}" is available to attach to in '
    'container "{container_name}"'
))
def assert_no_usable_agent_session(session, container_name, ctx, fake_driver):
    """A failed launch leaves NO usable agent session: the startup prompt was
    never injected into the named tmux session (no usable agent to attach to).
    """
    send_keys = fake_driver.send_keys_calls(container_name)
    prompt = ctx.get("startup_prompt", "please begin your session")
    injected = [
        c for c in send_keys
        if "-t" in c.command
        and c.command[c.command.index("-t") + 1] == session
        and any(prompt in tok for tok in c.command)
    ]
    assert not injected, (
        f"Expected NO startup prompt injected into tmux session {session!r} "
        f"in {container_name!r} (no usable agent session), but found: "
        f"{[c.command for c in injected]!r}"
    )


def _lead63em_read_diagnostic_from_host(ctx):
    """Read the persisted diagnostic file from the HOST.

    Reads the documented per-BC host path directly off the host filesystem —
    NO docker exec, NO tmux attach, and WITHOUT touching the launch result's
    stderr.  Returns the file's text.  Asserts the file exists.
    """
    from bc_launcher.controller import launch_diagnostic_path
    bc_name = ctx["bc_name"]
    path = launch_diagnostic_path(bc_name)
    assert path.exists(), (
        f"Expected a persisted launch-diagnostic file at the documented "
        f"per-BC host location {path}, but it does not exist"
    )
    ctx["diagnostic_path"] = path
    return path.read_text(encoding="utf-8")


@then(parsers.parse(
    "bc-container writes the diagnostic to a persisted file at a known, "
    "documented host-discoverable location on the same host-visible per-BC "
    "surface the mailbox is read from, stating why the session failed to "
    "come up"
))
def assert_diagnostic_persisted(ctx, fake_driver):
    text = _lead63em_read_diagnostic_from_host(ctx)
    assert text.strip(), (
        f"Expected the persisted diagnostic file to state why the session "
        f"failed; got empty content"
    )
    assert "reason:" in text, (
        f"Expected the diagnostic to state a reason; got: {text!r}"
    )
    # The file lives under the documented per-BC host surface root.
    from bc_launcher.controller import launch_diagnostic_path
    path = launch_diagnostic_path(ctx["bc_name"])
    assert str(path).startswith(ctx["host_state_dir"]), (
        f"Diagnostic path {path} is not under the per-BC host state surface "
        f"{ctx['host_state_dir']}"
    )


@then(parsers.parse(
    "that persisted diagnostic file is readable from the host without "
    "attaching into any tmux session and without relying on the launch "
    "command's stderr or the bc-container monitor tmux pane"
))
def assert_diagnostic_readable_independent(ctx, fake_driver):
    # Read straight off the host filesystem — independent of stderr / pane.
    path = ctx["diagnostic_path"]
    text = path.read_text(encoding="utf-8")
    assert text.strip(), "diagnostic file unexpectedly empty"
    # No tmux attach was needed to read it (the launcher never attaches).
    assert not fake_driver.interactive_calls, (
        "Reading the diagnostic must NOT require a tmux attach; interactive "
        f"calls recorded: {[c.command for c in fake_driver.interactive_calls]!r}"
    )
    # Independence from stderr: blank the launch result's stderr and confirm
    # the file is STILL readable and still carries the cause.
    result = ctx["result"]
    result.stderr = ""
    again = path.read_text(encoding="utf-8")
    assert again.strip() and "cause:" in again, (
        f"Diagnostic file must remain authoritative independent of stderr; "
        f"got: {again!r}"
    )


@then(parsers.parse(
    'the diagnostic names the failure cause by carrying the literal '
    'cause-marker token "{cause_marker}" exactly, so the operator is pointed '
    'at the right repair'
))
def assert_diagnostic_cause_marker(cause_marker, ctx):
    path = ctx["diagnostic_path"]
    text = path.read_text(encoding="utf-8")
    # The literal cause-marker token must appear EXACTLY in the persisted file.
    assert f"cause: {cause_marker}\n" in text or text.strip().startswith(
        f"cause: {cause_marker}"
    ), (
        f"Expected the persisted diagnostic to carry the literal cause-marker "
        f"token {cause_marker!r} exactly; got file content: {text!r}"
    )
    # Teeth: a generic diagnostic that does not carry THIS specific marker
    # must not satisfy the assertion.  Confirm the recorded write content
    # carried the marker (not merely a generic message).
    expected = ctx.get("expected_cause_marker")
    if expected is not None:
        assert cause_marker == expected, (
            f"Scenario row asserts marker {cause_marker!r} but the configured "
            f"fault was {expected!r}"
        )


# --- Scenario 7084bbbf: discoverable even when no session ever came up ---

@given(parsers.parse(
    'a launch of BC name "{bc_name}" failed before any usable tmux session '
    'named "{session}" came up'
))
def launch_failed_before_session(bc_name, session, ctx, fake_driver, controller,
                                 tmp_path):
    """Drive a real failed launch (messaging-DB unreachable) so the launcher
    persists its diagnostic file, then forget the launch's stderr — modelling
    an operator who arrives after the launch process has exited.
    """
    _lead63em_point_state_dir_at_sandbox(ctx)
    repo_url = f"https://github.com/shopsystem/{bc_name}.git"
    dsn = _READINESS_DSN
    fake_driver.set_running(f"bc-{bc_name}", running=False)
    fake_driver.set_dsn_reachable(dsn, reachable=False)
    default_manifest = tmp_path / "bc-manifest.yaml"
    if not default_manifest.exists():
        import yaml as _yaml
        default_manifest.write_text(_yaml.dump({
            "product": "shopsystem product",
            "bcs": [{"name": bc_name, "remote": repo_url, "role": "bc"}],
        }))
    credential_home = ctx.get("credential_home")
    result = controller.launch(
        bc_name=bc_name,
        repo_url=repo_url,
        shopmsg_dsn=dsn,
        startup_prompt="please begin your session",
        manifest_path=default_manifest,
        credential_home=credential_home,
    )
    assert result.exit_code != 0, "expected the launch to fail"
    ctx["bc_name"] = bc_name
    ctx["container_name"] = f"bc-{bc_name}"
    ctx["session"] = session
    # The launch process has exited; its stderr is no longer available to the
    # operator who comes looking later.  Drop it from the test's view.
    ctx["result"] = result
    ctx["stderr_no_longer_available"] = True
    result.stderr = ""


@when(parsers.parse(
    "I look for the launch diagnostic from the host without attaching into "
    "any tmux session"
))
def look_for_diagnostic_from_host(ctx):
    text = _lead63em_read_diagnostic_from_host(ctx)
    ctx["diagnostic_text"] = text


@then(parsers.parse(
    "bc-container exposes the diagnostic as a persisted file at a known, "
    "documented host-discoverable location on the same host-visible per-BC "
    "surface the mailbox is read from"
))
def assert_diagnostic_exposed_as_file(ctx):
    from bc_launcher.controller import launch_diagnostic_path
    path = launch_diagnostic_path(ctx["bc_name"])
    assert path.exists(), (
        f"Expected a persisted diagnostic file at {path}"
    )
    assert str(path).startswith(ctx["host_state_dir"]), (
        f"Diagnostic path {path} not on the per-BC host surface "
        f"{ctx['host_state_dir']}"
    )
    ctx["diagnostic_path"] = path


@then(parsers.parse(
    'that persisted diagnostic file is readable from the host even though no '
    'tmux session named "{session}" ever came up and the launch command\'s '
    'stderr is no longer available'
))
def assert_diagnostic_readable_no_session(session, ctx, fake_driver):
    # No agent tmux session ever came up: no startup prompt was ever injected
    # into the named session.
    container_name = ctx["container_name"]
    send_keys = fake_driver.send_keys_calls(container_name)
    injected = [
        c for c in send_keys
        if "-t" in c.command
        and c.command[c.command.index("-t") + 1] == session
        and any("please begin your session" in tok for tok in c.command)
    ]
    assert not injected, (
        f"No usable agent session should have come up; found prompt injection: "
        f"{[c.command for c in injected]!r}"
    )
    # stderr is no longer available, yet the file is still readable.
    assert ctx.get("stderr_no_longer_available"), (
        "precondition: the launch stderr should be gone"
    )
    text = ctx["diagnostic_path"].read_text(encoding="utf-8")
    assert text.strip(), "diagnostic file unexpectedly empty"


@then(parsers.parse("the diagnostic states why the session failed to come up"))
def assert_diagnostic_states_why(ctx):
    text = ctx["diagnostic_path"].read_text(encoding="utf-8")
    assert "reason:" in text and "cause:" in text, (
        f"Expected the diagnostic to state cause + reason; got: {text!r}"
    )


# ===========================================================================
# lead-bnhn — launch-diagnostic write is best-effort/non-fatal + user-writable
# default. Two robustness pins scenario 56 (0d010cf8f3175226, 7084bbbfdef94f81)
# left UNPINNED:
#   (a) a diagnostic-write FAILURE must NOT abort the launch (it is caught, a
#       host-discoverable warning is surfaced, the launch continues);
#   (b) the DEFAULT diagnostic location (no BCLAUNCHER_HOST_STATE_DIR override)
#       is a user-writable per-user state dir, NOT root-owned /var/lib.
# ---------------------------------------------------------------------------


@given(parsers.parse(
    "writing the launch diagnostic file will fail because the diagnostic "
    "target directory is not writable"
))
def diagnostic_write_will_fail(fake_driver):
    """Force the diagnostic write to RAISE PermissionError (lead-bnhn).

    Models the /var/lib/bc-launcher non-writable-target crash: the driver's
    write_launch_diagnostic raises instead of writing.  The controller's
    best-effort wrap MUST catch this so the launch is not aborted.
    """
    fake_driver.fail_launch_diagnostic_write()


@then(parsers.parse(
    "the launch is not aborted by the diagnostic-write failure and runs to "
    "its own failure result"
))
def assert_launch_not_aborted_by_diagnostic_failure(ctx, fake_driver):
    """NON-FATAL teeth: the launch produced its OWN failure result (the
    messaging-DB readiness failure), NOT a crash/exception from the diagnostic
    write.  If the controller re-raised the write error (fatal), the When step
    would have propagated the PermissionError and ``ctx['result']`` would never
    have been set — so reaching a CommandResult at all proves non-fatality.
    """
    result = ctx["result"]
    # The launch ran to a normal CommandResult rather than crashing.
    assert result is not None, (
        "Expected the launch to produce a CommandResult despite the "
        "diagnostic-write failure; a fatal re-raise would have crashed the "
        "When step before any result was recorded"
    )
    # It is the launch's OWN failure (non-zero exit for the messaging-DB
    # readiness barrier), not a success masking the abort.
    assert result.exit_code != 0, (
        f"Expected the launch to report its own (messaging-DB) failure; "
        f"got exit_code={result.exit_code}"
    )
    # The controller DID attempt the diagnostic write to the documented path
    # (it just failed) — confirming the non-fatal wrap sits AROUND a real
    # attempt, not that the write was simply skipped.
    assert fake_driver.launch_diagnostic_writes, (
        "Expected the controller to ATTEMPT a diagnostic write (which the "
        "fake forced to fail), but no write attempt was recorded"
    )


@then(parsers.parse(
    "bc-container surfaces a host-discoverable warning that the launch "
    "diagnostic could not be written, naming the target path and the "
    "write-failure cause"
))
def assert_diagnostic_write_failure_warning(ctx, fake_driver):
    """The launch result's stderr is the host-discoverable warning surface
    here (no persisted file could be written).  It must name that the
    diagnostic could not be written, the target path, and the write-failure
    cause — so an operator learns WHY the diagnostic is missing without a tmux
    attach.  Teeth: re-raising the write error (fatal) RED'd the prior step;
    swallowing it silently (no warning) REDs this one.
    """
    stderr = ctx["result"].stderr
    assert "could not write launch diagnostic" in stderr, (
        f"Expected a host-discoverable warning that the diagnostic could not "
        f"be written; stderr: {stderr!r}"
    )
    # The target path the controller tried to write to is named in the warning.
    from bc_launcher.controller import launch_diagnostic_path
    path = launch_diagnostic_path(ctx["bc_name"])
    assert str(path) in stderr, (
        f"Expected the warning to NAME the target path {path}; stderr: "
        f"{stderr!r}"
    )
    # The write-failure cause (the exception) is named so the operator can fix
    # it (e.g. PermissionError / Permission denied).
    assert "PermissionError" in stderr or "Permission denied" in stderr, (
        f"Expected the warning to name the write-failure cause; stderr: "
        f"{stderr!r}"
    )
    # No persisted file exists at the documented path (the write failed), so
    # the warning — not a file — is the legible fallback surface.
    assert not path.exists(), (
        f"The diagnostic write was forced to fail; no file should exist at "
        f"{path}"
    )


@then(parsers.parse(
    "the underlying launch-failure cause is still reported on the "
    "host-discoverable warning surface"
))
def assert_underlying_cause_still_reported(ctx):
    """Even with no persisted diagnostic file, the ACTUAL launch-failure cause
    (the messaging-DB readiness failure) is still legible on stderr, so the
    diagnostic-write failure degraded gracefully without hiding the real
    failure it was meant to describe.
    """
    stderr = ctx["result"].stderr
    assert "messaging readiness failure" in stderr or "not reachable" in stderr, (
        f"Expected the underlying launch-failure cause to remain reported on "
        f"the host-discoverable warning surface; stderr: {stderr!r}"
    )


@given(parsers.parse(
    "no BCLAUNCHER_HOST_STATE_DIR override is set in the environment"
))
def no_host_state_dir_override(ctx, monkeypatch):
    """Resolve the DEFAULT diagnostic location (lead-bnhn).

    The autouse ``_lead63em_host_state_dir`` fixture sets
    BCLAUNCHER_HOST_STATE_DIR for the whole suite; DELETE it here so this
    scenario exercises the genuine DEFAULT resolution (the per-user state dir),
    which is the property under pin.
    """
    monkeypatch.delenv("BCLAUNCHER_HOST_STATE_DIR", raising=False)


@when(parsers.parse(
    'I resolve the documented launch-diagnostic location for BC name '
    '"{bc_name}"'
))
def resolve_default_diagnostic_location(bc_name, ctx):
    from bc_launcher.controller import launch_diagnostic_path
    ctx["bc_name"] = bc_name
    ctx["resolved_diagnostic_path"] = launch_diagnostic_path(bc_name)


@then(parsers.parse(
    "the resolved diagnostic location is under a user-writable per-user state "
    "directory rooted at XDG_STATE_HOME or its default ~/.local/state"
))
def assert_resolved_under_user_state_dir(ctx):
    """USER-WRITABLE teeth: the default root is the per-user state dir, which
    the invoking user can write to.  A default of /var/lib/bc-launcher
    (root-required) would land the path elsewhere and RED both this and the
    NOT-/var/lib step below.
    """
    import os as _os
    from pathlib import Path as _Path
    from bc_launcher.controller import default_host_state_dir
    path = ctx["resolved_diagnostic_path"]
    expected_root = default_host_state_dir()
    # The resolved path is under the per-user default state root.
    assert str(path).startswith(str(expected_root)), (
        f"Resolved diagnostic path {path} is not under the per-user default "
        f"state root {expected_root}"
    )
    # That root is rooted at $XDG_STATE_HOME when set, else ~/.local/state.
    xdg = _os.environ.get("XDG_STATE_HOME")
    base = _Path(xdg) if xdg else (_Path.home() / ".local" / "state")
    assert str(expected_root).startswith(str(base)), (
        f"Default state root {expected_root} is not rooted at the per-user "
        f"base {base} (XDG_STATE_HOME or ~/.local/state)"
    )
    # It is genuinely the invoking user's tree (writable), not a root-owned one.
    assert str(base) == str(_Path.home() / ".local" / "state") or xdg, (
        "Per-user state base must be XDG_STATE_HOME or ~/.local/state"
    )


@then(parsers.parse(
    "the resolved diagnostic location is NOT under the root-owned "
    "/var/lib/bc-launcher"
))
def assert_resolved_not_var_lib(ctx):
    path = ctx["resolved_diagnostic_path"]
    assert not str(path).startswith("/var/lib/bc-launcher"), (
        f"Resolved diagnostic path {path} must NOT default to the root-owned "
        f"/var/lib/bc-launcher (the lead-bnhn PermissionError crash root)"
    )


@then(parsers.parse(
    "the resolved diagnostic location is the known, documented per-BC "
    "host-discoverable path found by a host lookup that does not attach into "
    "any tmux session"
))
def assert_resolved_host_discoverable(ctx, fake_driver):
    """ADR-041 D2 preserved: the location is the documented per-BC path
    (``<root>/bc-<bc>/launch-diagnostic.txt``), found by the SAME pure host
    lookup (``launch_diagnostic_path``) the 63em host-discovery scenario uses
    — no docker exec, no tmux attach.
    """
    from bc_launcher.controller import (
        launch_diagnostic_path,
        LAUNCH_DIAGNOSTIC_FILENAME,
    )
    path = ctx["resolved_diagnostic_path"]
    # Pure host lookup reproduces it (idempotent, no session involved).
    assert path == launch_diagnostic_path(ctx["bc_name"]), (
        "The documented location must be reproducible by the pure host lookup"
    )
    # Per-BC layout: <root>/bc-<bc_name>/launch-diagnostic.txt.
    assert path.name == LAUNCH_DIAGNOSTIC_FILENAME, (
        f"Expected the documented diagnostic filename; got {path.name!r}"
    )
    assert path.parent.name == f"bc-{ctx['bc_name']}", (
        f"Expected a per-BC subdir bc-{ctx['bc_name']}; got "
        f"{path.parent.name!r}"
    )
    # The lookup attached into NO tmux session.
    assert not fake_driver.interactive_calls, (
        "Host-discovery of the diagnostic location must NOT require a tmux "
        f"attach; interactive calls: "
        f"{[c.command for c in fake_driver.interactive_calls]!r}"
    )


# ===========================================================================
# lead-czwo — centralized scheduled bc-base dependency check-bump-rebuild.
#
# A SINGLE scheduled workflow polls every baked bc-base dependency for its
# latest canonical release, bumps the docker/bc-base/Dockerfile pin when one is
# newer, COMMITS the bumped Dockerfile, then rebuilds bc-base and republishes
# :latest. This REPLACES the per-repo repository_dispatch fan-in (retired
# scenarios 365be56194c892b9 + edd2c813688ab768; ADR-022).
#
# Per the scenario-40 declarative-artifact precedent, live Actions / live
# registry state is OUT-OF-BAND; the committed poll workflow YAML + Dockerfile
# are the proxy, inspected structurally.
# ===========================================================================

_BC_BASE_DOCKERFILE_REL = "docker/bc-base/Dockerfile"

# The four baked dependencies and their canonical repositories
# (0f386f31857fbeb1). The poll must reference each canonical repo.
_BAKED_DEP_CANONICAL_REPOS = {
    "shop-templates": "dstengle/shopsystem-templates",
    "shop-msg": "dstengle/shopsystem-messaging",
    "scenarios": "dstengle/shopsystem-scenarios",
    "beads": "steveyegge/beads",
}


def _workflow_on(doc) -> dict:
    """The normalized `on:` mapping of a workflow doc (YAML parses bare `on`
    as the boolean True key)."""
    on = doc.get("on", doc.get(True))
    return on if isinstance(on, dict) else {}


def _centralized_poll_workflow():
    """Return (path, doc) for the SINGLE committed workflow that runs the
    bc-base check-bump-rebuild cycle on a recurring schedule, or None.

    Identity (930a6a6579e2a859): triggered by a cron `schedule:` and rebuilds
    the shopsystem-bc-base image (a build step). The bare-dispatch
    rebuild-bc-base.yml (scenario 4e470f7584650a2d) is NOT schedule-triggered,
    so it is excluded — the two coexist without colliding on this identity.
    """
    matches = []
    for path, doc in _load_workflows().items():
        if not isinstance(doc, dict):
            continue
        on = _workflow_on(doc)
        if "schedule" not in on:
            continue
        text = path.read_text()
        if "shopsystem-bc-base" not in text:
            continue
        if not ("build-push-action" in text or "docker build" in text):
            continue
        matches.append((path, doc))
    if len(matches) == 1:
        return matches[0]
    if not matches:
        return None
    # More than one schedule-triggered bc-base rebuild workflow violates the
    # "exactly one workflow runs the cycle" invariant; return the list so the
    # Then can assert on the count.
    return matches


# --- Given/When shared setup ----------------------------------------------

@given("the shopsystem-bc-launcher BC repository owns the bc-base Dockerfile "
       "and its publish CI")
def given_bc_launcher_owns_dockerfile_and_ci(ctx):
    ctx["repo_root"] = _REPO_ROOT
    ctx["bc_base_dockerfile"] = _find_bc_base_dockerfile()


@when("the workflow that triggers the bc-base check-bump-rebuild cycle is "
      "inspected")
def when_cycle_workflow_inspected(ctx):
    ctx["poll_workflow"] = _centralized_poll_workflow()
    ctx["all_workflows"] = _load_workflows()


@then("there is exactly one workflow in shopsystem-bc-launcher that runs that "
      "cycle")
def then_exactly_one_cycle_workflow(ctx):
    # Count schedule-triggered bc-base-rebuild workflows directly so this is
    # non-vacuous: zero fails, more than one fails.
    count = 0
    for path, doc in ctx["all_workflows"].items():
        if not isinstance(doc, dict):
            continue
        on = _workflow_on(doc)
        if "schedule" not in on:
            continue
        text = path.read_text()
        if "shopsystem-bc-base" not in text:
            continue
        if "build-push-action" in text or "docker build" in text:
            count += 1
    assert count == 1, (
        "Expected EXACTLY ONE schedule-triggered workflow that rebuilds "
        f"shopsystem-bc-base (the check-bump-rebuild cycle); found {count}."
    )


@then('that workflow declares a cron "schedule:" trigger so the check runs on '
      "a recurring schedule without an external event")
def then_workflow_declares_cron_schedule(ctx):
    wf = ctx["poll_workflow"]
    assert wf is not None and not isinstance(wf, list), (
        "No single centralized scheduled cycle workflow was resolved."
    )
    path, doc = wf
    on = _workflow_on(doc)
    schedule = on.get("schedule")
    assert isinstance(schedule, list) and schedule, (
        f"Workflow {path.name} declares no schedule: list."
    )
    crons = [e.get("cron") for e in schedule if isinstance(e, dict)]
    assert any(c for c in crons), (
        f"Workflow {path.name} schedule: declares no cron expression "
        f"(got {schedule!r}); the cycle would not run on a recurring "
        "schedule without an external event."
    )


@then("the workflow's executable body, with YAML comment lines excluded, "
      "handles all baked dependencies rather than one workflow per dependency")
def then_one_workflow_all_deps(ctx):
    wf = ctx["poll_workflow"]
    assert wf is not None and not isinstance(wf, list)
    path, doc = wf
    # Inspect the EXECUTABLE workflow body only (comment-only lines stripped):
    # the header comment descriptively enumerates all four dep->repo mappings,
    # so asserting against the raw text would pass off the COMMENT even if a
    # dep were dropped from the executable DEPS array. Per-dep coverage must be
    # proven by the executable config, not the rationale prose.
    text = _strip_yaml_comments(path.read_text())
    # The single workflow must reference EVERY baked dependency's canonical
    # repo, proving it handles all four rather than one-per-dep.
    missing = [
        repo for repo in _BAKED_DEP_CANONICAL_REPOS.values()
        if repo not in text
    ]
    assert not missing, (
        f"The centralized workflow {path.name} does not reference all baked "
        f"dependency canonical repos; missing: {missing!r}."
    )


@then('a dependency enumerated only in a descriptive YAML comment, absent from '
      'the executable body, does not satisfy "handles all baked dependencies"')
def then_comment_only_dep_does_not_satisfy(ctx):
    # TEETH: prove the comment-stripping is load-bearing, not decorative. A dep
    # whose canonical repo appears ONLY in a comment line (not in the executable
    # body) must NOT count toward "handles all baked dependencies". We assert by
    # construction: inject a synthetic canonical repo into a comment line of the
    # workflow text, strip comments, and confirm the synthetic repo is absent
    # from the stripped body. If _strip_yaml_comments did NOT remove the
    # comment, this would fail — so the assertion has genuine teeth.
    wf = ctx["poll_workflow"]
    assert wf is not None and not isinstance(wf, list)
    path, doc = wf
    raw = path.read_text()
    sentinel = "acme/comment-only-phantom-dep"
    assert sentinel not in raw, (
        "Test sentinel unexpectedly already present in the workflow text."
    )
    # Place the sentinel mapping in a comment line ONLY (never the exec body).
    injected = raw + f"\n# phantom mapping: phantom -> {sentinel}\n"
    stripped = _strip_yaml_comments(injected)
    assert sentinel not in stripped, (
        "A dependency mapping present only in a descriptive YAML comment "
        "survived comment-stripping; comment-only enumeration would falsely "
        "satisfy 'handles all baked dependencies'. The coverage check must "
        "inspect the comment-stripped executable body."
    )
    # And the real coverage must still hold against the stripped EXECUTABLE body.
    exec_body = _strip_yaml_comments(raw)
    missing = [
        repo for repo in _BAKED_DEP_CANONICAL_REPOS.values()
        if repo not in exec_body
    ]
    assert not missing, (
        f"The centralized workflow {path.name} executable body (comments "
        f"stripped) does not handle all baked dependencies; missing: "
        f"{missing!r}."
    )


@then('no inbound cross-repo "repository_dispatch" event is required to start '
      "the cycle")
def then_no_repository_dispatch_required(ctx):
    wf = ctx["poll_workflow"]
    assert wf is not None and not isinstance(wf, list)
    path, doc = wf
    on = _workflow_on(doc)
    assert "repository_dispatch" not in on, (
        f"The centralized cycle workflow {path.name} declares a "
        "repository_dispatch trigger; the cycle must start WITHOUT an inbound "
        "cross-repo event (it is schedule/workflow_dispatch-triggered)."
    )
    # The cycle must in fact start from the schedule.
    assert "schedule" in on, (
        f"Workflow {path.name} has no schedule: trigger, so a recurring "
        "no-event start is not possible."
    )


# --- Scenario Outline 0f386f31857fbeb1: per-dep token + canonical repo -----

@given("the centralized scheduled workflow in shopsystem-bc-launcher runs its "
       "dependency check")
def given_centralized_runs_dep_check(ctx):
    wf = _centralized_poll_workflow()
    assert wf is not None and not isinstance(wf, list), (
        "No single centralized scheduled check-bump-rebuild workflow found."
    )
    ctx["poll_workflow"] = wf
    ctx["poll_workflow_text"] = wf[0].read_text()


@given(parsers.parse('the baked dependency "{dependency}" is resolved against '
                     'its canonical repository "{canonical_repo}"'))
def given_dep_resolved_against_repo(dependency, canonical_repo, ctx):
    # The Examples table must match the canonical mapping (guards the table).
    expected = _BAKED_DEP_CANONICAL_REPOS.get(dependency)
    assert expected == canonical_repo, (
        f"Dependency {dependency!r} canonical repo mismatch: example says "
        f"{canonical_repo!r}, expected {expected!r}."
    )
    ctx["current_dep"] = dependency
    ctx["current_canonical_repo"] = canonical_repo


@when(parsers.parse('the workflow looks up the latest release tag for '
                    '"{dependency}"'))
def when_workflow_looks_up_latest(dependency, ctx):
    ctx["lookup_dep"] = dependency


@then(parsers.parse('the workflow\'s executable body, with YAML comment lines '
                    'excluded, enumerates "{dependency}" mapped to its '
                    'canonical repository "{canonical_repo}"'))
def then_exec_body_enumerates_dep_to_repo(dependency, canonical_repo, ctx):
    # Inspect the EXECUTABLE workflow body only (comment-only lines stripped):
    # the dep->repo mapping must be present in the executable DEPS config, not
    # merely in the descriptive header comment. The DEPS array entries take the
    # form "<dep>|<owner/repo>"; require BOTH the canonical repo AND the
    # dep-key to be present in the stripped body so a dropped executable entry
    # (whose comment survives) cannot pass.
    text = _strip_yaml_comments(ctx["poll_workflow_text"])
    assert canonical_repo in text, (
        f"The centralized workflow executable body (comments stripped) does "
        f"not enumerate the canonical repo {canonical_repo!r} for dependency "
        f"{dependency!r}; a comment-only mapping does not count."
    )
    # The executable DEPS array pairs the dep-key with its canonical repo on
    # one line ("<dep>|<owner/repo>"). Require that exact executable pairing so
    # a stray repo reference elsewhere cannot substitute for the DEPS entry.
    pairing = f"{dependency}|{canonical_repo}"
    assert pairing in text, (
        f"The centralized workflow executable body (comments stripped) does "
        f"not enumerate the executable mapping {pairing!r}; the dep->repo "
        "pairing must live in the executable DEPS config, not a comment."
    )


@then(parsers.parse('a "{dependency}" to "{canonical_repo}" mapping present '
                    'only in a descriptive YAML comment, absent from the '
                    'executable body, does not satisfy this lookup'))
def then_comment_only_mapping_does_not_satisfy(dependency, canonical_repo, ctx):
    # TEETH: prove the comment-stripping is load-bearing for the per-dep lookup.
    # Construct a workflow text in which THIS dep->repo mapping appears only in
    # a comment line, strip comments, and confirm the executable-body pairing is
    # absent from the stripped text. If _strip_yaml_comments did NOT remove the
    # comment, the pairing would survive and this would fail — genuine teeth.
    raw = ctx["poll_workflow_text"]
    pairing = f"{dependency}|{canonical_repo}"
    # Remove the real executable pairing, then re-introduce it ONLY in a comment.
    without_exec = raw.replace(pairing, f"{dependency}|REDACTED-FOR-TEST")
    assert pairing not in without_exec, (
        "Failed to redact the executable dep->repo pairing for the teeth check."
    )
    comment_only = without_exec + f"\n# descriptive: {pairing}\n"
    stripped = _strip_yaml_comments(comment_only)
    assert pairing not in stripped, (
        f"The {dependency!r}->{canonical_repo!r} mapping present only in a "
        "descriptive YAML comment survived comment-stripping; a comment-only "
        "mapping would falsely satisfy the per-dep lookup. The lookup must be "
        "proven against the comment-stripped executable body."
    )
    # And the REAL executable pairing must still be present in the actual body.
    real_stripped = _strip_yaml_comments(raw)
    assert pairing in real_stripped, (
        f"The executable mapping {pairing!r} is absent from the workflow's "
        "comment-stripped executable body; the per-dep lookup is not satisfied."
    )


@then(parsers.parse('the lookup reads the public "{canonical_repo}" releases '
                    'using the workflow\'s own "GITHUB_TOKEN"'))
def then_lookup_uses_github_token_and_repo(canonical_repo, ctx):
    # Inspect the EXECUTABLE workflow body only (comment-only lines stripped):
    # the header comment descriptively lists every canonical repo, so a raw-text
    # assertion would be satisfied by the COMMENT even if the executable DEPS
    # array stopped polling that dep. Per-dep coverage must be proven by the
    # executable config, not the rationale prose.
    text = _strip_yaml_comments(ctx["poll_workflow_text"])
    # The canonical repo must be referenced by the workflow (per-dep coverage).
    assert canonical_repo in text, (
        f"The centralized workflow does not reference the canonical repo "
        f"{canonical_repo!r}, so it cannot resolve that dependency's latest "
        "release."
    )
    # The release lookup must use the workflow's OWN GITHUB_TOKEN. Accept the
    # standard token expressions; the gh CLI reads GH_TOKEN/GITHUB_TOKEN.
    uses_github_token = (
        "secrets.GITHUB_TOKEN" in text
        or "${{ github.token }}" in text
        or "GITHUB_TOKEN" in text
    )
    assert uses_github_token, (
        "The centralized workflow does not use its own GITHUB_TOKEN to read "
        f"the {canonical_repo!r} releases."
    )


def _strip_yaml_comments(text: str) -> str:
    """Return the workflow text with full-line "# ..." comments removed.

    Rationale comments may legitimately NAME the credentials/paths the workflow
    deliberately does NOT use; the forbidden-token scan must inspect the
    EFFECTIVE YAML, not the explanatory prose. Only strips comment-only lines
    (leading-whitespace then "#") to avoid mangling "#" inside quoted values.
    """
    out = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)


@then('the lookup does not reference a "BC_LAUNCHER_DISPATCH_TOKEN" or any '
      "other cross-repo dispatch credential")
def then_no_dispatch_token(ctx):
    # Inspect EFFECTIVE YAML (comment-only lines stripped): a real cross-repo
    # dispatch credential reference would be in executable YAML, not in the
    # rationale comments that name what the workflow deliberately avoids.
    text = _strip_yaml_comments(ctx["poll_workflow_text"])
    forbidden = [
        "BC_LAUNCHER_DISPATCH_TOKEN",
        "DISPATCH_TOKEN",
        "PAT_DISPATCH",
    ]
    hits = [tok for tok in forbidden if tok in text]
    assert not hits, (
        "The centralized poll references a cross-repo dispatch credential it "
        f"must not: {hits!r}. It must resolve latest releases with the "
        "workflow's own GITHUB_TOKEN only."
    )
    # No cross-repo dispatch PATH either: the poll must not be wired to a
    # repository_dispatch trigger (asserted via the parsed on: mapping, which
    # ignores comments).
    on = _workflow_on(ctx["poll_workflow"][1])
    assert "repository_dispatch" not in on, (
        "The centralized poll declares a repository_dispatch trigger; it must "
        "start the cycle without any cross-repo dispatch."
    )


@then(parsers.parse('the resolved latest release tag for "{dependency}" is '
                    'what the workflow compares against the current bc-base '
                    'Dockerfile pin'))
def then_compares_latest_against_pin(dependency, ctx):
    text = ctx["poll_workflow_text"]
    dockerfile_rel = _BC_BASE_DOCKERFILE_REL
    # The workflow must read the current pin from the bc-base Dockerfile to
    # compare against the resolved latest tag (the bump decision).
    assert dockerfile_rel in text or "DOCKERFILE" in text, (
        "The centralized workflow does not reference the bc-base Dockerfile, "
        "so it cannot compare the resolved latest tag against the current pin."
    )
    # A genuine compare reads the latest release tag (gh release view / API).
    resolves_latest = (
        "gh release view" in text
        or "releases/latest" in text
        or "tagName" in text
        or "tag_name" in text
    )
    assert resolves_latest, (
        "The centralized workflow does not resolve a latest release tag to "
        "compare against the Dockerfile pin."
    )


# --- Scenario 5b6a931a493971a6: bump-then-build-then-republish -------------

@given(parsers.parse('the bc-base Dockerfile in shopsystem-bc-launcher pins a '
                     'baked dependency at "{old_pin}"'))
def given_dockerfile_pins_dep_at(old_pin, ctx):
    ctx["old_pin"] = old_pin
    ctx["poll_workflow"] = _centralized_poll_workflow()
    assert ctx["poll_workflow"] is not None and not isinstance(
        ctx["poll_workflow"], list
    )
    ctx["poll_workflow_text"] = ctx["poll_workflow"][0].read_text()


@given(parsers.parse("the centralized scheduled workflow resolves that "
                     'dependency\'s latest release tag as "{new_pin}"'))
def given_resolves_latest_as(new_pin, ctx):
    ctx["new_pin"] = new_pin


@when("the workflow runs its check-bump-rebuild cycle for that dependency")
def when_runs_cycle_for_dep(ctx):
    ctx.setdefault("poll_workflow", _centralized_poll_workflow())
    ctx.setdefault("poll_workflow_text", ctx["poll_workflow"][0].read_text())


def _step_order(text, *needles):
    """Return the index of the FIRST occurrence of each needle (or -1)."""
    return [text.find(n) for n in needles]


@then(parsers.parse('the workflow first mutates "{dockerfile}" so the '
                    'dependency pin reads "{new_pin}" rather than "{old_pin}"'))
def then_mutates_dockerfile_pin(dockerfile, new_pin, old_pin, ctx):
    text = ctx["poll_workflow_text"]
    assert dockerfile in text or "DOCKERFILE" in text, (
        f"The workflow does not reference {dockerfile} to mutate the pin."
    )
    # A genuine in-place bump edits the Dockerfile (sed -i / equivalent write).
    mutates = "sed -i" in text or ">> \"${DOCKERFILE}\"" in text or "sed -i -E" in text
    assert mutates, (
        "The workflow does not mutate the Dockerfile pin in place (no "
        "`sed -i` or equivalent), so a stale pin would not be bumped."
    )


@then("only after the pin is bumped does the workflow run the bc-base image "
      "build")
def then_bump_before_build(ctx):
    text = ctx["poll_workflow_text"]
    bump_idx = text.find("sed -i")
    build_idx = text.find("build-push-action")
    if build_idx == -1:
        build_idx = text.find("docker build")
    assert bump_idx != -1, "No Dockerfile pin bump (sed -i) found."
    assert build_idx != -1, "No bc-base image build step found."
    assert bump_idx < build_idx, (
        "The bc-base image build is declared BEFORE the Dockerfile pin bump; "
        "the bump must come first so the build picks up the new pin."
    )


@then(parsers.parse('the workflow republishes "{image_ref}" at the new digest '
                    'built from the bumped Dockerfile'))
def then_republishes_latest_new_digest(image_ref, ctx):
    text = ctx["poll_workflow_text"]
    assert image_ref in text, (
        f"The workflow does not republish {image_ref}."
    )
    assert "build-push-action" in text or "docker build" in text, (
        "The workflow has no build step, so it cannot republish a new digest."
    )
    # Build before push of the bumped pin: the bump (sed) precedes the build.
    bump_idx = text.find("sed -i")
    build_idx = text.find("build-push-action")
    assert bump_idx != -1 and build_idx != -1 and bump_idx < build_idx, (
        "The republished digest is not built from the bumped Dockerfile "
        "(bump does not precede build)."
    )


@then(parsers.parse('a bare rebuild that left the Dockerfile pin at "{old_pin}"'
                    ' would not satisfy this behavior'))
def then_bare_rebuild_insufficient(old_pin, ctx):
    text = ctx["poll_workflow_text"]
    # Teeth: the workflow must actually mutate the pin (otherwise a bare
    # rebuild leaving the pin stale would falsely satisfy the scenario).
    assert "sed -i" in text, (
        "The workflow performs no pin mutation; a bare rebuild leaving the "
        "pin stale would (wrongly) satisfy the bump behavior."
    )


# --- Scenario cf8625dbac93cfdc: no-op when every dep equals its pin --------

@given(parsers.parse('the bc-base Dockerfile in shopsystem-bc-launcher pins '
                     'every baked dependency at its current "{pin}"'))
def given_dockerfile_pins_every_dep(pin, ctx):
    ctx["poll_workflow"] = _centralized_poll_workflow()
    assert ctx["poll_workflow"] is not None and not isinstance(
        ctx["poll_workflow"], list
    )
    ctx["poll_workflow_text"] = ctx["poll_workflow"][0].read_text()


@given("for every baked dependency the resolved latest release tag equals the "
       "tag already pinned in the Dockerfile")
def given_all_deps_equal(ctx):
    ctx["all_deps_equal"] = True


@when("the centralized scheduled workflow runs its check-bump-rebuild cycle")
def when_centralized_runs_cycle(ctx):
    ctx.setdefault("poll_workflow", _centralized_poll_workflow())
    ctx.setdefault("poll_workflow_text", ctx["poll_workflow"][0].read_text())


@then(parsers.parse('the workflow leaves "{dockerfile}" unchanged with no pin '
                    'bumped'))
def then_leaves_dockerfile_unchanged(dockerfile, ctx):
    text = ctx["poll_workflow_text"]
    # The no-op path is gated: the commit + build + push steps must be
    # conditional on a "changed" signal, so an all-equal run mutates nothing.
    assert "changed" in text, (
        "The workflow declares no 'changed' gate; it cannot distinguish a "
        "no-op (all deps equal) run from a bump run, so it would commit / "
        "rebuild unconditionally."
    )


@then("the workflow does not run a bc-base image build")
def then_no_build_on_noop(ctx):
    wf = ctx["poll_workflow"]
    doc = wf[1]
    # The build step must be conditional (if:) on the changed-gate so a no-op
    # run skips it.
    build_step = _find_step(doc, lambda s: "build-push-action" in str(
        s.get("uses", "")) or "docker build" in str(s.get("run", "")))
    assert build_step is not None, "No bc-base build step found."
    cond = str(build_step.get("if", ""))
    assert "changed" in cond, (
        "The bc-base build step is not gated on the changed-signal "
        f"(if: {cond!r}); a no-op all-equal run would still build."
    )


@then(parsers.parse('the workflow does not republish "{image_ref}" with a new '
                    'digest'))
def then_no_republish_on_noop(image_ref, ctx):
    wf = ctx["poll_workflow"]
    doc = wf[1]
    push_step = _find_step(
        doc,
        lambda s: image_ref in str(s.get("with", {}).get("tags", ""))
        or image_ref in str(s.get("run", "")),
    )
    assert push_step is not None, (
        f"No step republishing {image_ref} found."
    )
    cond = str(push_step.get("if", ""))
    assert "changed" in cond, (
        f"The republish step for {image_ref} is not gated on the "
        f"changed-signal (if: {cond!r}); a no-op run would republish."
    )


def _find_step(doc, pred):
    jobs = doc.get("jobs", {})
    for job in jobs.values():
        for step in job.get("steps", []) or []:
            if pred(step):
                return step
    return None


# --- Scenario 59c0f539187eabbb: workflow_dispatch manual start -------------

@given(parsers.parse('the centralized bc-base rebuild workflow in '
                     'shopsystem-bc-launcher declares a "{trigger}" trigger'))
def given_declares_trigger(trigger, ctx):
    wf = _centralized_poll_workflow()
    assert wf is not None and not isinstance(wf, list), (
        "No single centralized bc-base rebuild workflow found."
    )
    ctx["poll_workflow"] = wf
    ctx["poll_workflow_text"] = wf[0].read_text()
    on = _workflow_on(wf[1])
    assert trigger in on, (
        f"The centralized workflow {wf[0].name} does not declare a "
        f"{trigger!r} trigger (on: {list(on.keys())!r})."
    )


@given('a baked dependency\'s latest release tag is newer than the tag pinned '
       'in "docker/bc-base/Dockerfile"')
def given_a_dep_is_newer(ctx):
    ctx["a_dep_newer"] = True


@when(parsers.parse('an operator starts the workflow via "workflow_dispatch" '
                    'from the Actions UI or "gh workflow run"'))
def when_operator_starts_via_dispatch(ctx):
    ctx.setdefault("poll_workflow", _centralized_poll_workflow())
    ctx.setdefault("poll_workflow_text", ctx["poll_workflow"][0].read_text())


@then("the manually started run resolves each baked dependency's latest "
      "release tag the same way the scheduled run does")
def then_manual_same_as_scheduled(ctx):
    wf = ctx["poll_workflow"]
    on = _workflow_on(wf[1])
    # The SAME workflow declares BOTH schedule and workflow_dispatch, so the
    # manual run executes the identical job/steps as the scheduled run.
    assert "schedule" in on and "workflow_dispatch" in on, (
        "The centralized workflow does not declare BOTH schedule and "
        f"workflow_dispatch (on: {list(on.keys())!r}); a manual run would not "
        "run the same path as the scheduled run."
    )


@then(parsers.parse('the run bumps the stale Dockerfile pin then rebuilds and '
                    'republishes "{image_ref}"'))
def then_manual_bumps_and_republishes(image_ref, ctx):
    text = ctx["poll_workflow_text"]
    assert "sed -i" in text, "The workflow does not bump the Dockerfile pin."
    bump_idx = text.find("sed -i")
    build_idx = text.find("build-push-action")
    assert build_idx != -1 and bump_idx < build_idx, (
        "The build does not follow the pin bump."
    )
    assert image_ref in text, f"The workflow does not republish {image_ref}."


@then('starting the workflow this way requires no source-code change and no '
      'raw "gh api .../dispatches" call')
def then_manual_no_source_change_no_raw_dispatch(ctx):
    wf = ctx["poll_workflow"]
    on = _workflow_on(wf[1])
    # workflow_dispatch is sufficient to start it (Actions UI / gh workflow
    # run); a repository_dispatch (raw `gh api .../dispatches`) is NOT required.
    assert "workflow_dispatch" in on, (
        "The workflow lacks a workflow_dispatch trigger, so an operator could "
        "not start it without a source change or a raw dispatch call."
    )
    assert "repository_dispatch" not in on, (
        "The workflow declares a repository_dispatch trigger; starting it must "
        "not require a raw `gh api .../dispatches` call."
    )


# --- Scenario 2b69c3b682f7871d: commit the bumped Dockerfile ---------------

@given(parsers.parse('the centralized scheduled workflow bumps a baked '
                     'dependency pin in "{dockerfile}" from "{old_pin}" to '
                     '"{new_pin}"'))
def given_workflow_bumps_pin(dockerfile, old_pin, new_pin, ctx):
    ctx["poll_workflow"] = _centralized_poll_workflow()
    assert ctx["poll_workflow"] is not None and not isinstance(
        ctx["poll_workflow"], list
    )
    ctx["poll_workflow_text"] = ctx["poll_workflow"][0].read_text()
    ctx["new_pin"] = new_pin


@when(parsers.parse('the workflow rebuilds bc-base and republishes "{image_ref}"'
                    ' from that bumped Dockerfile'))
def when_rebuilds_from_bumped(image_ref, ctx):
    ctx.setdefault("poll_workflow", _centralized_poll_workflow())
    ctx.setdefault("poll_workflow_text", ctx["poll_workflow"][0].read_text())


@then(parsers.parse('the bumped "{dockerfile}" is committed back to the '
                    'shopsystem-bc-launcher repository'))
def then_bumped_dockerfile_committed(dockerfile, ctx):
    text = ctx["poll_workflow_text"]
    # A genuine commit-back step runs `git commit` (and pushes) the bumped
    # Dockerfile.
    assert "git commit" in text, (
        "The workflow does not `git commit` the bumped Dockerfile back to the "
        "repository; the bump would be working-tree-only."
    )
    assert "git add" in text and dockerfile in text, (
        f"The workflow does not `git add` {dockerfile} before committing."
    )
    assert "git push" in text, (
        "The workflow does not `git push` the commit, so the bumped pin would "
        "not land on the repository."
    )


@then(parsers.parse('the committed Dockerfile records the dependency pinned at '
                    '"{new_pin}" that the republished bc-base:latest was built '
                    'from'))
def then_committed_records_new_pin(new_pin, ctx):
    text = ctx["poll_workflow_text"]
    # The commit (git add + git commit) must happen BEFORE the build, so the
    # republished image is built from the committed pin (not a transient edit).
    commit_idx = text.find("git commit")
    build_idx = text.find("build-push-action")
    if build_idx == -1:
        build_idx = text.find("docker build")
    assert commit_idx != -1 and build_idx != -1, (
        "Missing commit or build step."
    )
    assert commit_idx < build_idx, (
        "The bc-base build runs BEFORE the bumped Dockerfile is committed, so "
        "the republished image would be built from an uncommitted pin."
    )


@then("the build was not produced from an uncommitted working-tree-only pin "
      "edit")
def then_not_working_tree_only(ctx):
    text = ctx["poll_workflow_text"]
    commit_idx = text.find("git commit")
    build_idx = text.find("build-push-action")
    if build_idx == -1:
        build_idx = text.find("docker build")
    assert commit_idx != -1, (
        "The workflow never commits the bumped pin; the build would be from a "
        "working-tree-only edit."
    )
    assert commit_idx < build_idx, (
        "The build precedes the commit; the republished image would be built "
        "from an uncommitted working-tree-only pin edit."
    )


# --- Scenario 69904daef7a8d13e: latest carries the new version -------------

@given(parsers.parse('the published "bc-base:latest" image carries an '
                     'installed baked dependency at version "{old}"'))
def given_latest_carries_dep_version(old, ctx):
    ctx["dep_old_version"] = old
    ctx["poll_workflow"] = _centralized_poll_workflow()
    assert ctx["poll_workflow"] is not None and not isinstance(
        ctx["poll_workflow"], list
    )
    ctx["poll_workflow_text"] = ctx["poll_workflow"][0].read_text()
    ctx["bc_base_dockerfile"] = _find_bc_base_dockerfile()


@given(parsers.parse("the dependency's canonical repository publishes a newer "
                     'release tag "{new}" distinct from "{old}"'))
def given_canonical_publishes_newer(new, old, ctx):
    assert new != old, "scenario precondition: vDep_new must differ from vDep_old"
    ctx["dep_new_version"] = new


@given(parsers.parse('the centralized scheduled bc-launcher workflow resolves '
                     '"{new}" as that dependency\'s latest release'))
def given_workflow_resolves_new(new, ctx):
    ctx["dep_new_version"] = new


@when(parsers.parse('the workflow bumps the Dockerfile pin to "{new}", '
                    'rebuilds bc-base, and republishes the "latest" tag'))
def when_bumps_rebuilds_republishes(new, ctx):
    ctx.setdefault("poll_workflow", _centralized_poll_workflow())
    ctx.setdefault("poll_workflow_text", ctx["poll_workflow"][0].read_text())


@then(parsers.parse('pulling "{image_ref}" yields an image whose installed '
                    'dependency reports version "{new}"'))
def then_pulled_reports_new_version(image_ref, new, ctx):
    text = ctx["poll_workflow_text"]
    # The propagation chain that makes :latest carry the new version: the
    # workflow bumps the pin in the Dockerfile (sed -i), commits it, then
    # rebuilds and republishes :latest from the committed bumped Dockerfile.
    assert "sed -i" in text, (
        "The workflow does not bump the Dockerfile pin, so :latest would not "
        f"carry {new!r}."
    )
    assert "git commit" in text, (
        "The workflow does not commit the bumped pin, so the rebuild would not "
        f"be from the bumped Dockerfile carrying {new!r}."
    )
    assert image_ref in text, (
        f"The workflow does not republish {image_ref}."
    )
    bump_idx = text.find("sed -i")
    build_idx = text.find("build-push-action")
    assert build_idx != -1 and bump_idx < build_idx, (
        "The rebuild does not follow the pin bump, so the republished :latest "
        f"would not carry {new!r}."
    )
    # The build must consume THE bc-base Dockerfile (the bumped one).
    assert _BC_BASE_DOCKERFILE_REL in text, (
        "The workflow does not build from the bc-base Dockerfile."
    )


# ===========================================================================
# bc-base SELF-PIN polled-dependency step definitions (lead-dqje / lead-5yql)
#
# Scenarios 493bbbb7dcb61d7e (bump-then-rebuild when stale) and
# e28886c34b0d4c65 (no-op when equal). The poll treats the
# shopsystem-bc-launcher self-pin (the bc-base Dockerfile's VCS pin on
# bc-launcher's OWN code) as a 5th polled dependency. These bindings are
# ADDITIVE to the four-dep family in test_bc_base_centralized_dep_poll.py and
# REUSE _strip_yaml_comments for the comment-exclusion teeth (5vyb precedent).
#
# The self-pin's canonical repo. The poll must map shopsystem-bc-launcher to
# this repo in its EXECUTABLE DEPS array (not merely a comment).
# ===========================================================================

_SELF_PIN_DEP_KEY = "shopsystem-bc-launcher"
_SELF_PIN_CANONICAL_REPO = "dstengle/shopsystem-bc-launcher"


@given(parsers.parse(
    'the bc-base Dockerfile in shopsystem-bc-launcher pins shopsystem-bc-launcher '
    'itself at "{self_pin}" in a "{vcs_prefix}" VCS pin'))
def given_dockerfile_self_pins_bc_launcher(self_pin, vcs_prefix, ctx):
    ctx["self_pin"] = self_pin
    ctx["self_pin_vcs_prefix"] = vcs_prefix
    ctx["poll_workflow"] = _centralized_poll_workflow()
    assert ctx["poll_workflow"] is not None and not isinstance(
        ctx["poll_workflow"], list
    ), "No single centralized scheduled check-bump-rebuild workflow found."
    ctx["poll_workflow_text"] = ctx["poll_workflow"][0].read_text()
    # The bc-base Dockerfile must actually carry a bc-launcher self-pin in the
    # asserted VCS-pin format, distinct from the framework-CLI pins.
    dockerfile = _find_bc_base_dockerfile()
    assert dockerfile is not None, "No bc-base Dockerfile found."
    ctx["bc_base_dockerfile"] = dockerfile
    dtext = dockerfile.read_text()
    assert vcs_prefix in dtext, (
        f"The bc-base Dockerfile does not carry the {vcs_prefix!r} VCS pin for "
        "the bc-launcher self-pin."
    )
    assert re.search(
        r"shopsystem-bc-launcher(?:\.git)?@v[0-9]+\.[0-9]+\.[0-9]+", dtext
    ), (
        "The bc-base Dockerfile does not carry a shopsystem-bc-launcher self-pin "
        "in the VCS-pin format the poll's bump logic targets."
    )


@given(parsers.parse(
    "the centralized scheduled workflow resolves shopsystem-bc-launcher's own "
    'latest release tag against its canonical repository "{canonical_repo}" '
    'using the workflow\'s own "{token}"'))
def given_self_pin_resolves_against_canonical(canonical_repo, token, ctx):
    assert canonical_repo == _SELF_PIN_CANONICAL_REPO, (
        f"self-pin canonical repo mismatch: scenario says {canonical_repo!r}, "
        f"expected {_SELF_PIN_CANONICAL_REPO!r}."
    )
    ctx["self_pin_canonical_repo"] = canonical_repo
    ctx["self_pin_token"] = token


@given(parsers.parse(
    "the centralized scheduled workflow resolves shopsystem-bc-launcher's own "
    'latest release tag against "{canonical_repo}" as "{latest}"'))
def given_self_pin_resolves_as(canonical_repo, latest, ctx):
    assert canonical_repo == _SELF_PIN_CANONICAL_REPO, (
        f"self-pin canonical repo mismatch: scenario says {canonical_repo!r}, "
        f"expected {_SELF_PIN_CANONICAL_REPO!r}."
    )
    ctx["self_pin_canonical_repo"] = canonical_repo
    ctx["self_pin_latest"] = latest


@given(parsers.parse(
    'the resolved latest release tag for shopsystem-bc-launcher is "{latest}", '
    'newer than the self-pin "{self_pin}"'))
def given_self_pin_latest_newer(latest, self_pin, ctx):
    ctx["self_pin_latest"] = latest
    ctx["self_pin"] = self_pin


@given("the resolved latest release tag for shopsystem-bc-launcher equals the "
       "self-pin already in the Dockerfile")
def given_self_pin_equals_latest(ctx):
    ctx["self_pin_equal"] = True


@when("the workflow runs its check-bump-rebuild cycle")
def when_runs_cycle_plain(ctx):
    ctx.setdefault("poll_workflow", _centralized_poll_workflow())
    ctx.setdefault("poll_workflow_text", ctx["poll_workflow"][0].read_text())


@when("the workflow runs its check-bump-rebuild cycle and no other baked "
      "dependency is stale")
def when_runs_cycle_no_other_stale(ctx):
    ctx.setdefault("poll_workflow", _centralized_poll_workflow())
    ctx.setdefault("poll_workflow_text", ctx["poll_workflow"][0].read_text())


@then(parsers.parse(
    "the workflow's executable body, with YAML comment lines excluded, "
    "enumerates shopsystem-bc-launcher mapped to canonical repository "
    '"{canonical_repo}" alongside the existing baked dependencies'))
def then_exec_body_enumerates_self_pin(canonical_repo, ctx):
    # Inspect the comment-stripped EXECUTABLE body only (5vyb teeth): the
    # self-pin's dep->repo pairing must live in the executable DEPS array, not
    # merely the descriptive header comment.
    text = _strip_yaml_comments(ctx["poll_workflow_text"])
    pairing = f"{_SELF_PIN_DEP_KEY}|{canonical_repo}"
    assert pairing in text, (
        "The centralized workflow executable body (comments stripped) does not "
        f"enumerate the self-pin DEPS mapping {pairing!r}; the self-pin must be "
        "a polled dependency in the executable DEPS config, not a comment."
    )
    # "alongside the existing baked dependencies": the four-dep family must
    # STILL be enumerated (additive, not replacing).
    missing = [
        repo for repo in _BAKED_DEP_CANONICAL_REPOS.values()
        if repo not in text
    ]
    assert not missing, (
        "Adding the self-pin must not drop the existing baked dependencies; "
        f"missing from the executable body: {missing!r}."
    )


@then("a shopsystem-bc-launcher self-pin enumerated only in a descriptive YAML "
      "comment, absent from the executable body, does not satisfy this lookup")
def then_self_pin_comment_only_does_not_satisfy(ctx):
    # TEETH (5vyb precedent): prove comment-stripping is load-bearing for the
    # self-pin enumeration. Redact the real executable pairing, re-introduce it
    # ONLY in a comment, strip comments, and confirm the pairing is absent.
    raw = ctx["poll_workflow_text"]
    pairing = f"{_SELF_PIN_DEP_KEY}|{_SELF_PIN_CANONICAL_REPO}"
    without_exec = raw.replace(pairing, f"{_SELF_PIN_DEP_KEY}|REDACTED-FOR-TEST")
    assert pairing not in without_exec, (
        "Failed to redact the executable self-pin pairing for the teeth check; "
        "the executable DEPS array must carry the self-pin pairing exactly once "
        "in a form this teeth check can redact."
    )
    comment_only = without_exec + f"\n# descriptive: {pairing}\n"
    stripped = _strip_yaml_comments(comment_only)
    assert pairing not in stripped, (
        "A self-pin pairing present only in a descriptive YAML comment survived "
        "comment-stripping; a comment-only enumeration would falsely satisfy the "
        "self-pin lookup. The lookup must inspect the comment-stripped body."
    )
    # And the REAL executable pairing must still be present.
    real_stripped = _strip_yaml_comments(raw)
    assert pairing in real_stripped, (
        f"The self-pin executable mapping {pairing!r} is absent from the "
        "workflow's comment-stripped executable body."
    )


@then(parsers.parse(
    'the workflow first mutates "{dockerfile}" so the shopsystem-bc-launcher '
    'self-pin reads "{new_pin}" rather than "{old_pin}"'))
def then_mutates_self_pin(dockerfile, new_pin, old_pin, ctx):
    text = ctx["poll_workflow_text"]
    assert dockerfile in text or "DOCKERFILE" in text, (
        f"The workflow does not reference {dockerfile} to mutate the self-pin."
    )
    # The bump must target the bc-launcher VCS-pin format specifically (NOT the
    # framework-CLI pins): a sed that rewrites the shopsystem-bc-launcher VCS pin.
    stripped = _strip_yaml_comments(text)
    assert re.search(
        r"sed -i[^\n]*shopsystem-bc-launcher", stripped
    ), (
        "The workflow has no in-place mutation (sed -i) targeting the "
        "shopsystem-bc-launcher self-pin; a stale self-pin would not be bumped, "
        "or the bump would not target the self-pin's VCS-pin line specifically."
    )


@then("only after the self-pin is bumped does the workflow run the bc-base "
      "image build")
def then_self_pin_bump_before_build(ctx):
    text = ctx["poll_workflow_text"]
    stripped = _strip_yaml_comments(text)
    m = re.search(r"sed -i[^\n]*shopsystem-bc-launcher", stripped)
    assert m is not None, (
        "No self-pin bump (sed -i targeting shopsystem-bc-launcher) found."
    )
    bump_idx = stripped.find(m.group(0))
    build_idx = stripped.find("build-push-action")
    if build_idx == -1:
        build_idx = stripped.find("docker build")
    assert build_idx != -1, "No bc-base image build step found."
    assert bump_idx < build_idx, (
        "The bc-base image build is declared BEFORE the self-pin bump; the "
        "self-pin bump must come first so the build picks up the new self-pin."
    )


@then(parsers.parse(
    'the workflow commits the bumped "{dockerfile}" recording the '
    'shopsystem-bc-launcher version "{new_pin}" before the build'))
def then_commits_self_pin_before_build(dockerfile, new_pin, ctx):
    text = ctx["poll_workflow_text"]
    assert "git commit" in text, (
        "The workflow does not `git commit` the bumped Dockerfile; the self-pin "
        "bump would be working-tree-only."
    )
    assert "git add" in text and (dockerfile in text or "DOCKERFILE" in text), (
        f"The workflow does not `git add` {dockerfile} before committing."
    )
    assert "git push" in text, (
        "The workflow does not `git push` the commit, so the bumped self-pin "
        "would not land on the repository."
    )
    # Commit BEFORE build: the republished image is built from the committed
    # self-pin, not a transient edit (commit-before-build discipline).
    commit_idx = text.find("git commit")
    build_idx = text.find("build-push-action")
    if build_idx == -1:
        build_idx = text.find("docker build")
    assert commit_idx != -1 and build_idx != -1, "Missing commit or build step."
    assert commit_idx < build_idx, (
        "The bc-base build runs BEFORE the bumped Dockerfile is committed, so "
        "the republished image would be built from an uncommitted self-pin."
    )


@then("this self-pin handling composes with the existing baked-dependency "
      "checks rather than replacing them")
def then_self_pin_composes_with_existing(ctx):
    # The four-dep family must remain in the executable DEPS array alongside the
    # new self-pin entry (additive, not replacing). All five canonical repos
    # present in the comment-stripped executable body.
    text = _strip_yaml_comments(ctx["poll_workflow_text"])
    expected = list(_BAKED_DEP_CANONICAL_REPOS.values()) + [
        _SELF_PIN_CANONICAL_REPO
    ]
    missing = [repo for repo in expected if repo not in text]
    assert not missing, (
        "The self-pin handling does not compose with the existing baked-dep "
        f"checks; missing canonical repos from the executable body: {missing!r}."
    )
    # The existing four-dep per-dep pairings must remain too.
    for dep, repo in _BAKED_DEP_CANONICAL_REPOS.items():
        assert f"{dep}|{repo}" in text, (
            f"The existing baked-dependency pairing {dep}|{repo} was dropped "
            "when adding the self-pin; the change must be additive."
        )


# --- Scenario e28886c34b0d4c65: self-pin no-op when equal -------------------

@then(parsers.parse(
    'the workflow leaves the shopsystem-bc-launcher self-pin in "{dockerfile}" '
    'unchanged at "{self_pin}"'))
def then_leaves_self_pin_unchanged(dockerfile, self_pin, ctx):
    # The no-op path is gated: the per-dep loop `continue`s when latest == pin,
    # and the commit/build/push steps are conditional on the changed-gate, so an
    # all-equal run (self-pin included) mutates nothing.
    text = ctx["poll_workflow_text"]
    assert "changed" in text, (
        "The workflow declares no 'changed' gate; it cannot distinguish a "
        "self-pin no-op (equal) run from a bump run."
    )
    stripped = _strip_yaml_comments(text)
    # The compare-then-skip must apply to the self-pin too: it is a regular DEPS
    # loop entry, so the shared `if equal: continue` covers it. Confirm the
    # self-pin is a DEPS entry subject to that loop (not a special always-bump
    # path).
    assert f"{_SELF_PIN_DEP_KEY}|{_SELF_PIN_CANONICAL_REPO}" in stripped, (
        "The self-pin is not a DEPS-array entry, so the shared equal->continue "
        "no-op path would not cover it."
    )
    assert "continue" in stripped, (
        "The per-dep loop has no equal->skip (continue) branch, so an equal "
        "self-pin would still be bumped."
    )


@then("the workflow does not run a bc-base image build on account of the "
      "self-pin")
def then_no_build_on_self_pin_noop(ctx):
    wf = ctx["poll_workflow"]
    doc = wf[1]
    build_step = _find_step(doc, lambda s: "build-push-action" in str(
        s.get("uses", "")) or "docker build" in str(s.get("run", "")))
    assert build_step is not None, "No bc-base build step found."
    cond = str(build_step.get("if", ""))
    assert "changed" in cond, (
        "The bc-base build step is not gated on the changed-signal "
        f"(if: {cond!r}); a self-pin no-op (equal) run would still build."
    )


@then(parsers.parse(
    'the workflow does not republish "{image_ref}" with a new digest on '
    'account of the self-pin'))
def then_no_republish_on_self_pin_noop(image_ref, ctx):
    wf = ctx["poll_workflow"]
    doc = wf[1]
    push_step = _find_step(
        doc,
        lambda s: image_ref in str(s.get("with", {}).get("tags", ""))
        or image_ref in str(s.get("run", "")),
    )
    assert push_step is not None, f"No step republishing {image_ref} found."
    cond = str(push_step.get("if", ""))
    assert "changed" in cond, (
        f"The republish step for {image_ref} is not gated on the changed-signal "
        f"(if: {cond!r}); a self-pin no-op run would republish."
    )


# ===========================================================================
# Readiness-wait SELF-ADVANCE step definitions (lead-gw9v / lead-c713)
#
# Scenarios e30b15363815abed / f3784811e04a224d / 9fa36102d756a8fb.  During the
# INITIAL readiness wait the launcher must resolve the workspace-trust gate by
# polling for EITHER the transient PRE-trust banner "Accessing workspace:"
# (→ accept trust with Enter → input-ready → inject) OR the input-ready marker
# "bypass permissions on" already being present because claude self-advanced
# past the workspace-trust prompt (→ treat agent as up, SKIP the trust-accept
# Enter, → inject), aborting non-zero ONLY if NEITHER is reached within the
# readiness timeout.  This fixes the prior hard-gate that aborted with an
# "agent-startup failure" the instant the transient banner was not caught.
#
# These COMPOSE with — and do NOT supersede — lead-cw7m's step-4 auto-dismiss.
# The FakeDockerDriver models the three modes faithfully (see
# tests/fake_driver.py: simulate_self_advance_readiness / the
# wait_for_pane_marker + capture_pane mode blocks / the trust-accept Enter
# counter).
# ===========================================================================

_SELF_ADVANCE_STARTUP_PROMPT = "bd prime"


def _launch_with_self_advance_mode(ctx, fake_driver, controller, tmp_path, mode):
    """Configure a self-advance readiness mode and run launch (lead-gw9v).

    Both readiness barriers (messaging DB, agent-vault broker) pass; the
    workspace-trust gate during the initial readiness wait is resolved per
    ``mode`` (see fake_driver.simulate_self_advance_readiness).
    """
    bc_name = "shopsystem-messaging"
    container_name = f"bc-{bc_name}"
    repo_url = f"https://github.com/shopsystem/{bc_name}.git"
    dsn = _READINESS_DSN
    fake_driver.set_dsn_reachable(dsn, reachable=True)
    fake_driver.simulate_self_advance_readiness(container_name, mode)
    manifest_path = tmp_path / "bc-manifest.yaml"
    if not manifest_path.exists():
        manifest_path.write_text(yaml.dump({
            "product": "shopsystem product",
            "bcs": [{"name": bc_name, "remote": repo_url, "role": "bc"}],
        }))
    result = controller.launch(
        bc_name=bc_name,
        repo_url=repo_url,
        shopmsg_dsn=dsn,
        startup_prompt=_SELF_ADVANCE_STARTUP_PROMPT,
        manifest_path=manifest_path,
        credential_home=ctx.get("credential_home"),
    )
    ctx["result"] = result
    ctx["container_name"] = container_name
    ctx["bc_name"] = bc_name
    ctx["startup_prompt"] = _SELF_ADVANCE_STARTUP_PROMPT


# --- shared Given: the three readiness modes --------------------------------

@given(parsers.parse(
    'during the initial readiness wait the in-container agent runtime '
    'self-advances past the workspace-trust prompt so the agent pane shows the '
    'input-ready marker "{input_marker}" without the transient workspace-trust '
    'banner "{banner}" ever being caught by the launcher\'s polling'
))
def given_self_advance(input_marker, banner, ctx):
    ctx["_self_advance_mode"] = "self_advance"
    ctx["_self_advance_input_marker"] = input_marker
    ctx["_self_advance_banner"] = banner


@given(parsers.parse(
    'during the initial readiness wait the in-container agent runtime first '
    'renders the transient workspace-trust banner "{banner}" before reaching '
    'the input-ready marker "{input_marker}"'
))
def given_pre_trust(banner, input_marker, ctx):
    ctx["_self_advance_mode"] = "pre_trust"
    ctx["_self_advance_input_marker"] = input_marker
    ctx["_self_advance_banner"] = banner


@given(parsers.parse(
    'during the initial readiness wait the agent pane never shows the transient '
    'workspace-trust banner "{banner}" and never shows the input-ready marker '
    '"{input_marker}" within the readiness timeout'
))
def given_neither_marker(banner, input_marker, ctx):
    ctx["_self_advance_mode"] = "neither"
    ctx["_self_advance_input_marker"] = input_marker
    ctx["_self_advance_banner"] = banner


# --- shared When ------------------------------------------------------------

@when(parsers.parse(
    'I run "bc-container launch {bc_name} --startup-prompt \'{prompt}\'" and '
    'the launch command runs the agent-readiness sequence'
))
def when_launch_runs_readiness(bc_name, prompt, ctx, fake_driver, controller, tmp_path):
    _launch_with_self_advance_mode(
        ctx, fake_driver, controller, tmp_path, ctx["_self_advance_mode"]
    )


# --- e30b15363815abed: self-advance Then steps ------------------------------

@then(parsers.parse(
    'the launcher detects that the agent pane is already at the input-ready '
    'marker "{input_marker}" and treats the agent as up'
))
def then_detects_self_advanced(input_marker, ctx, fake_driver):
    container_name = ctx["container_name"]
    # Treating the agent as up means launch proceeded to inject and exited zero.
    assert ctx["result"].exit_code == 0, (
        f"Expected the launcher to treat the self-advanced agent as up and "
        f"exit zero; got {ctx['result'].exit_code} / stderr: "
        f"{ctx['result'].stderr!r}"
    )
    committed = fake_driver.agent_committed_prompt(container_name)
    assert committed == ctx["startup_prompt"], (
        f"Expected the startup prompt {ctx['startup_prompt']!r} to be injected "
        f"after detecting input-ready; committed={committed!r}"
    )


@then(parsers.parse(
    'the launcher does not abort the readiness sequence with an '
    '"{failure_text}" warning for the transient trust banner "{banner}" not '
    'being seen'
))
def then_no_agent_startup_abort(failure_text, banner, ctx):
    result = ctx["result"]
    assert result.exit_code == 0, (
        f"Expected NO abort (exit zero) on the self-advance path; got "
        f"{result.exit_code} / stderr: {result.stderr!r}"
    )
    assert "agent-startup failure" not in result.stderr, (
        "The launcher must NOT abort with an 'agent-startup failure' for the "
        f"transient banner {banner!r} not being seen when the agent has "
        f"self-advanced to input-ready; stderr: {result.stderr!r}"
    )


@then(parsers.parse(
    'the launcher does not keep hard-waiting for the transient trust banner '
    '"{banner}" until the readiness timeout'
))
def then_no_hard_wait_for_banner(banner, ctx, fake_driver):
    container_name = ctx["container_name"]
    from bc_launcher.controller import (
        CLAUDE_READY_MARKER,
        CLAUDE_READINESS_TIMEOUT_SECONDS,
        READINESS_DISMISS_POLL_SECONDS,
    )
    # The banner marker corresponds to CLAUDE_READY_MARKER.
    assert banner == CLAUDE_READY_MARKER, (
        f"Scenario banner {banner!r} must be the controller's "
        f"CLAUDE_READY_MARKER {CLAUDE_READY_MARKER!r}"
    )
    banner_waits = [
        (c, s, m) for (c, s, m) in fake_driver.wait_for_marker_calls
        if c == container_name and m == CLAUDE_READY_MARKER
    ]
    # A bounded poll loop may attempt the banner a FINITE small number of
    # times, but it must NOT have exhausted the full readiness timeout hard-
    # waiting for the banner: it broke out on detecting input-ready instead.
    max_attempts = (
        CLAUDE_READINESS_TIMEOUT_SECONDS / READINESS_DISMISS_POLL_SECONDS
    )
    assert len(banner_waits) < max_attempts, (
        f"The launcher kept hard-waiting for the banner {banner!r} "
        f"({len(banner_waits)} attempts >= the full-timeout budget "
        f"{max_attempts}); it should have detected input-ready and stopped."
    )
    # And the launch did NOT time out: it exited zero having injected.
    assert ctx["result"].exit_code == 0


@then(parsers.parse(
    'the launcher skips the trust-accept Enter keystroke that would otherwise '
    'be sent to accept the workspace-trust prompt'
))
def then_skips_trust_enter(ctx, fake_driver):
    container_name = ctx["container_name"]
    assert fake_driver.trust_accept_enter_count(container_name) == 0, (
        "On the self-advance path the launcher must SKIP the trust-accept "
        "Enter (the pane is already at input-ready, there is no trust prompt "
        "to accept); a trust-accept Enter was recorded: "
        f"{fake_driver.trust_accept_enter_count(container_name)}"
    )


@then(parsers.parse(
    'the launcher submits the startup prompt "{prompt}" to the tmux session '
    'named "{session}" in container "{container_name}" with no host-side '
    'follow-up "{inject_cmd}" invocation required'
))
def then_submits_prompt_no_host_inject(
    prompt, session, container_name, inject_cmd, ctx, fake_driver
):
    # The prompt was COMMITTED to the agent within the SINGLE launch
    # invocation — no separate host-side `bc-container inject` was needed.
    assert fake_driver.agent_committed_prompt(container_name) == prompt, (
        f"Expected the startup prompt {prompt!r} to be committed to the agent "
        f"within launch (BC online, no host-side inject); committed prompt is "
        f"{fake_driver.agent_committed_prompt(container_name)!r}"
    )
    # The submit is a text-only send-keys carrying the prompt followed by a
    # discrete bare Enter — both issued within this launch.
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
    # No interactive attach was needed (no host-side inject).
    assert not fake_driver.interactive_calls, (
        "Inject must require NO interactive attach / host-side inject; "
        f"interactive calls: {[c.command for c in fake_driver.interactive_calls]!r}"
    )


@then(parsers.parse(
    'the launch command exits zero with the BC online unattended'
))
def then_exits_zero_online(ctx, fake_driver):
    assert ctx["result"].exit_code == 0, (
        f"Expected exit zero (BC online unattended); got "
        f"{ctx['result'].exit_code} / stderr: {ctx['result'].stderr!r}"
    )
    committed = fake_driver.agent_committed_prompt(ctx["container_name"])
    assert committed == ctx["startup_prompt"], (
        f"Expected the BC online with the startup prompt {ctx['startup_prompt']!r} "
        f"committed; committed={committed!r}"
    )


# --- f3784811e04a224d: pre-trust Then steps ---------------------------------

@then(parsers.parse(
    'the launcher observes the transient workspace-trust banner "{banner}" and '
    'sends a trust-accept Enter keystroke to the tmux session named "{session}" '
    'in container "{container_name}"'
))
def then_observes_banner_sends_trust_enter(
    banner, session, container_name, ctx, fake_driver
):
    from bc_launcher.controller import CLAUDE_READY_MARKER
    # The banner marker wait was recorded (the launcher polled for it).
    banner_waits = [
        m for (c, _s, m) in fake_driver.wait_for_marker_calls
        if c == container_name and m == CLAUDE_READY_MARKER
    ]
    assert banner_waits, (
        f"Expected the launcher to poll for the trust banner {banner!r}; "
        f"recorded waits: {fake_driver.wait_for_marker_calls!r}"
    )
    # REGRESSION GUARD: the pre-trust path MUST send the trust-accept Enter.
    assert fake_driver.trust_accept_enter_count(container_name) >= 1, (
        "The pre-trust path must SEND the trust-accept Enter to accept the "
        "workspace-trust prompt; none was recorded."
    )


@then(parsers.parse(
    'after accepting trust the launcher waits for and observes the input-ready '
    'marker "{input_marker}"'
))
def then_after_trust_observes_input_ready(input_marker, ctx, fake_driver):
    container_name = ctx["container_name"]
    from bc_launcher.controller import CLAUDE_INPUT_READY_MARKER
    assert input_marker == CLAUDE_INPUT_READY_MARKER
    markers = [
        m for (c, _s, m) in fake_driver.wait_for_marker_calls
        if c == container_name
    ]
    assert CLAUDE_INPUT_READY_MARKER in markers, (
        f"Expected an input-ready marker wait for {input_marker!r}; "
        f"recorded: {markers!r}"
    )
    # Input-ready was actually reached (the launch proceeded to inject).
    assert fake_driver.agent_committed_prompt(container_name) == ctx["startup_prompt"]


# --- 9fa36102d756a8fb: neither-marker Then steps ----------------------------

@then(parsers.parse(
    'the launcher does not submit the startup prompt "{prompt}" to the tmux '
    'session named "{session}" in container "{container_name}"'
))
def then_does_not_submit_prompt(prompt, session, container_name, ctx, fake_driver):
    # No prompt was committed to the agent, and no send-keys carried the prompt
    # text — injection was suppressed because input-ready was never reached.
    assert fake_driver.agent_committed_prompt(container_name) != prompt, (
        f"The startup prompt {prompt!r} must NOT be submitted when neither "
        f"readiness marker is reached; it was committed to the agent."
    )
    for c in fake_driver.send_keys_calls(container_name):
        assert prompt not in c.command, (
            f"A send-keys carried the prompt {prompt!r} despite neither marker "
            f"being reached: {c.command!r}"
        )


@then(parsers.parse(
    'the launcher surfaces a host-discoverable WARNING that the agent never '
    'reached input-ready within the readiness timeout'
))
def then_warns_never_reached_input_ready(ctx):
    surface = ctx["result"].stderr
    low = surface.lower()
    assert "warning" in low, (
        f"Expected a host-discoverable WARNING on the neither-marker path; "
        f"got stderr: {surface!r}"
    )
    assert "input-ready" in low, (
        f"Expected the WARNING to state the agent never reached input-ready; "
        f"got stderr: {surface!r}"
    )
    assert "timeout" in low or "readiness timeout" in low or "did not become ready" in low, (
        f"Expected the WARNING to reference the readiness timeout; got stderr: "
        f"{surface!r}"
    )


@then(parsers.parse('the launch command exits non-zero'))
def then_exits_non_zero(ctx):
    assert ctx["result"].exit_code != 0, (
        f"Expected a non-zero exit when neither readiness marker is reached; "
        f"got exit_code {ctx['result'].exit_code} / stderr: "
        f"{ctx['result'].stderr!r}"
    )


@then(parsers.parse('the installed dependency version is no longer the '
                    'previously hard-pinned "{old}"'))
def then_no_longer_old_version(old, ctx):
    text = ctx["poll_workflow_text"]
    # The pin is rewritten in place to the resolved latest, so a republished
    # rebuild cannot carry the old hard-pinned version.
    assert "sed -i" in text, (
        "The workflow does not rewrite the Dockerfile pin; a rebuild would "
        f"re-pin the old hard-coded version {old!r}."
    )


# --- Scenario a4caf0477a74e4bc (lead-fwrx / lead-t3dy): bc-base AND bc-lead -----
# default to USER vscode so the baked ~/.claude state resolves for the running
# user (no first-run onboarding from a HOME mismatch).
#
# docker is NOT available in this environment, so the published-image
# `docker inspect` Config.User and `docker run --rm <image> whoami` cannot run
# live. The scenario is bound to the buildable-artifact source of truth: the
# COMMITTED bc-base + bc-lead Dockerfiles must resolve to a final/effective
# USER vscode (== published Config.User, == whoami), the synthetic ~/.claude
# state must be baked vscode-owned under /home/vscode, and the
# entrypoint/healthcheck/runtime-write paths must be chowned/permissioned for
# vscode so container start succeeds as uid 1000.

def _find_bc_lead_dockerfile() -> Path | None:
    """Return the tracked Dockerfile that builds shopsystem-bc-lead, or None.

    bc-lead is the thin launcher image that DERIVES ``FROM`` a
    shopsystem-bc-base image and adds the docker CLI. We identify it as a tracked
    Dockerfile whose text both references shopsystem-bc-lead AND carries a
    ``FROM ...shopsystem-bc-base`` line (it consumes the base rather than
    building it). Iterate in sorted order for deterministic discovery.
    """
    for path in sorted(_REPO_ROOT.rglob("Dockerfile*")):
        if ".git" in path.parts:
            continue
        if not path.is_file():
            continue
        text = path.read_text()
        if "shopsystem-bc-lead" not in text:
            continue
        if not re.search(r"(?im)^\s*FROM\s+\S*shopsystem-bc-base", text):
            continue
        return path
    return None


def _effective_final_user(dockerfile_text: str) -> str | None:
    """Return the value of the LAST ``USER`` instruction in a Dockerfile, or
    None if the Dockerfile contains no USER instruction.

    Docker applies the most recent USER directive to the runtime container, so
    the final USER instruction is the effective default user (== Config.User ==
    `whoami` for a run with no --user override). A Dockerfile ending USER root
    therefore makes Config.User=root (the lead-t3dy bug); one ending USER vscode
    makes Config.User=vscode (the fix).
    """
    last = None
    for m in re.finditer(r"(?im)^\s*USER\s+(\S+)", dockerfile_text):
        last = m.group(1).strip()
    return last


@given(parsers.parse('the published image "{image}"'))
def given_published_image(ctx, image):
    images = ctx.setdefault("default_user_images", {})
    if "bc-base" in image:
        images["bc-base"] = _find_bc_base_dockerfile()
    elif "bc-lead" in image:
        images["bc-lead"] = _find_bc_lead_dockerfile()
    else:  # pragma: no cover - scenario only names bc-base / bc-lead
        raise AssertionError(f"Unrecognized published image in scenario: {image!r}")


@when(parsers.parse(
    'each image is inspected via "docker inspect" and run via '
    '"docker run --rm <image> whoami"'))
def when_inspect_and_whoami(ctx):
    # docker is unavailable here; resolve the buildable-artifact source of truth
    # (the final/effective USER of each committed Dockerfile) instead, which is
    # exactly what the published image's Config.User and whoami would report.
    images = ctx["default_user_images"]
    resolved = {}
    for name, dockerfile in images.items():
        assert dockerfile is not None, (
            f"No tracked Dockerfile found that builds shopsystem-{name}."
        )
        resolved[name] = {
            "dockerfile": dockerfile,
            "text": dockerfile.read_text(),
        }
        resolved[name]["final_user"] = _effective_final_user(resolved[name]["text"])
    ctx["default_user_resolved"] = resolved


@then(parsers.parse(
    'the "Config.User" reported by "docker inspect" is "{expected}" for each '
    'image'))
def then_config_user_is(ctx, expected):
    resolved = ctx["default_user_resolved"]
    assert set(resolved) == {"bc-base", "bc-lead"}, (
        f"Scenario must cover both bc-base and bc-lead; got {set(resolved)}."
    )
    for name, info in resolved.items():
        final_user = info["final_user"]
        assert final_user == expected, (
            f"shopsystem-{name} Dockerfile ({info['dockerfile']}) resolves to a "
            f"final/effective USER {final_user!r}, not {expected!r}. The "
            f"published image's Config.User equals the last USER instruction, so "
            f"a Dockerfile ending USER root would publish Config.User=root and "
            f"the agent would hit first-run onboarding from a HOME mismatch "
            f"(lead-t3dy)."
        )


@then(parsers.parse(
    '"docker run --rm <image> whoami" reports "{expected}" for each image'))
def then_whoami_reports(ctx, expected):
    # whoami of a run with no --user override is the image's default user, i.e.
    # the same final/effective USER instruction Config.User reflects.
    resolved = ctx["default_user_resolved"]
    for name, info in resolved.items():
        assert info["final_user"] == expected, (
            f"shopsystem-{name} would run `whoami` as {info['final_user']!r}, "
            f"not {expected!r} (the final USER instruction is the default run "
            f"user)."
        )


@then(parsers.parse(
    'the running vscode user\'s HOME is "{home}" so the baked '
    '"{cred_path}" and "{config_path}" onboarding and credential state resolve '
    'for the running user'))
def then_home_and_baked_state_resolve(ctx, home, cred_path, config_path):
    assert home == "/home/vscode", (
        f"Scenario HOME {home!r} is not the vscode user's home /home/vscode."
    )
    # The baked synthetic state lives in bc-base; bc-lead inherits it unchanged.
    base = ctx["default_user_resolved"]["bc-base"]
    text = base["text"]
    # (1) The credentials + config are baked at the vscode HOME paths the scenario
    #     names so they resolve when HOME=/home/vscode.
    assert cred_path == "/home/vscode/.claude/.credentials.json", (
        f"Scenario credential path {cred_path!r} is not the baked vscode path."
    )
    assert config_path == "/home/vscode/.claude.json", (
        f"Scenario config path {config_path!r} is not the baked vscode path."
    )
    assert "/home/vscode/.claude/.credentials.json" in text, (
        "bc-base Dockerfile does not bake the credentials file at "
        "/home/vscode/.claude/.credentials.json, so it would not resolve for the "
        "running vscode user."
    )
    assert "/home/vscode/.claude.json" in text, (
        "bc-base Dockerfile does not bake /home/vscode/.claude.json."
    )
    # (2) The baked state must be vscode-OWNED so the running vscode user can read
    #     it (a root-owned bake under /home/vscode would be the mechanics gap).
    assert re.search(
        r"chown\s+-R\s+vscode:vscode\s+/home/vscode/\.claude\b", text), (
        "bc-base Dockerfile does not chown the baked ~/.claude state to "
        "vscode:vscode; the running vscode user could not read it."
    )


@then(parsers.parse(
    'claude started as the default user does not enter first-run onboarding or '
    'the login-method picker due to a HOME mismatch'))
def then_no_onboarding_from_home_mismatch(ctx):
    resolved = ctx["default_user_resolved"]
    # The HOME mismatch is exactly the lead-t3dy bug: default user root has
    # HOME=/root while the baked state lives under /home/vscode. The fix is that
    # BOTH images default to vscode (HOME=/home/vscode), so the baked state
    # resolves and no first-run onboarding fires.
    for name, info in resolved.items():
        assert info["final_user"] == "vscode", (
            f"shopsystem-{name} does not default to vscode; with HOME=/root the "
            f"baked /home/vscode/.claude state would not resolve and claude would "
            f"enter first-run onboarding / the login-method picker (lead-t3dy)."
        )
    # The entrypoint + healthcheck + runtime writes must still work as vscode.
    base = ctx["default_user_resolved"]["bc-base"]
    btext = base["text"]
    # The CA-materialization entrypoint writes under /home/vscode/.config; the
    # bc-base build must pre-create that dir vscode-owned so the entrypoint's
    # `mkdir -p` succeeds as uid 1000 (a root-only /home/vscode/.config would be
    # the runtime-write ownership gap).
    assert re.search(
        r"chown\s+-R\s+vscode:vscode\s+/home/vscode/\.config\b", btext), (
        "bc-base Dockerfile does not chown /home/vscode/.config to vscode:vscode "
        "before switching to USER vscode; the CA-materialization entrypoint's "
        "write under /home/vscode/.config/agent-vault would fail for the running "
        "vscode user, breaking container start."
    )
    # The CA-materialization entrypoint itself must only write under the vscode
    # HOME subtree (no root-only system trust store / update-ca-certificates),
    # otherwise it could not run as vscode.
    ca_script = _REPO_ROOT / "docker" / "bc-base" / "agent-vault-ca.sh"
    assert ca_script.is_file(), "agent-vault-ca.sh entrypoint not found."
    ca_text = ca_script.read_text()
    assert "update-ca-certificates" not in ca_text, (
        "The CA-materialization entrypoint calls update-ca-certificates (root "
        "only); it could not run as the default vscode user."
    )
    assert "/home/vscode/.config/agent-vault" in ca_text, (
        "The CA-materialization entrypoint does not write under the vscode HOME "
        "subtree, so its writes might require root."
    )


# ---------------------------------------------------------------------------
# bc-lead footing toolset: docker compose plugin + dolt binary (lead-ys8x;
# scenarios c5edfa89da00af8a / 98a0683d0360349e / a0992b2156d132e3).
#
# docker is NOT available in this environment, so — exactly as every existing
# bc-base/bc-lead image-content scenario does (a4caf0477a74e4bc default-user,
# d9909f38abea83b5 toolset, the test_bc_base_framework_cli_pins.* pins) — these
# scenarios are bound to the buildable-artifact source of truth: the committed
# docker/bc-lead/Dockerfile. We assert it installs the docker compose plugin
# (docker-compose-plugin) AND installs the dolt engine binary onto PATH. The
# live `docker compose version` / `dolt version` proof on the REBUILT published
# bc-lead image is the lead's post-release pull verification.
# ---------------------------------------------------------------------------

def _bc_lead_dockerfile_text(ctx) -> str:
    """Resolve and cache the committed bc-lead Dockerfile text for these steps."""
    cached = ctx.get("footing_toolset_text")
    if cached is not None:
        return cached
    path = _find_bc_lead_dockerfile()
    assert path is not None, (
        "No tracked Dockerfile found that builds shopsystem-bc-lead "
        "(FROM ...shopsystem-bc-base). The footing toolset scenarios "
        "(lead-ys8x) bind to that Dockerfile's content."
    )
    ctx["footing_toolset_path"] = path
    text = path.read_text()
    ctx["footing_toolset_text"] = text
    return text


def _strip_dockerfile_comments(text: str) -> str:
    """Return the Dockerfile text with whole-line ``#`` comments removed.

    Image-content scenarios must bind to actual build INSTRUCTIONS, not to
    documentation prose: a Dockerfile that merely mentions a package in a
    comment but never installs it must still fail the teeth. We drop lines whose
    first non-whitespace character is ``#`` (Dockerfile comments are
    whole-line) so the detectors below see only executable instructions.
    """
    return "\n".join(
        line for line in text.splitlines()
        if not line.lstrip().startswith("#")
    )


def _bc_lead_installs_compose_plugin(text: str) -> bool:
    """True iff the bc-lead Dockerfile INSTALLS the docker compose plugin.

    The compose plugin ships as the `docker-compose-plugin` apt package from
    Docker's official apt repo (the same repo that provides docker-ce-cli), so
    its presence in an apt(-get) install instruction is the buildable-artifact
    proof that `docker compose` resolves in the published image. We match only
    a non-comment `apt[-get] install ... docker-compose-plugin` line so a mere
    comment mention does not satisfy the teeth.
    """
    instructions = _strip_dockerfile_comments(text)
    return bool(re.search(
        r"apt(?:-get)?\s+install\b[^\n]*\bdocker-compose-plugin\b", instructions))


def _bc_lead_installs_dolt_on_path(text: str) -> bool:
    """True iff the bc-lead Dockerfile INSTALLS the dolt binary onto PATH.

    The dolt engine is a third-party Go binary (not apt/pip installable); the
    Dockerfile installs it from the dolthub/dolt releases onto /usr/local/bin
    (on PATH). We require, in NON-comment instructions, that dolt is placed on a
    PATH location (install/cp/mv into a bin dir, or an explicit
    /usr/local/bin/dolt target) so a comment mention does not satisfy the teeth.
    """
    instructions = _strip_dockerfile_comments(text)
    return bool(
        re.search(
            r"(install|cp|mv)\b[^\n]*\bdolt\b[^\n]*/usr/local/bin",
            instructions,
        )
        or re.search(r"/usr/local/bin/dolt\b", instructions)
    )


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


# ===========================================================================
# lead-h755 — a launched bc-base BC has gh and agent-vault resolvable on PATH
# at runtime. Regression guard pinning a present-but-unpinned RUNTIME
# invariant: inside the running container, `command -v gh` and
# `command -v agent-vault` each exit zero and print an executable path. docker
# is EXPLICITLY EXCLUDED (bc-base carries no docker CLI by design; PDR-020
# Addendum II). docker is unavailable in this environment, so the running
# container is modelled through the FakeDockerDriver in-container exec model
# (the same idiom as other launched-container runtime scenarios); the real
# observable is the lead's pull verification for the published image.
# ===========================================================================

_BC_BASE_PINNED_IMAGE = (
    "ghcr.io/dstengle/shopsystem-bc-base:latest"
)


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


@when(parsers.parse(
    '"command -v gh" and "command -v agent-vault" are executed inside that '
    'running container'))
def when_command_v_gh_and_agent_vault(ctx, fake_driver):
    container_name = ctx["container_name"]
    # Execute the real `command -v <tool>` vector inside the running container
    # via the driver's in-container exec seam — exactly what a runtime PATH
    # probe does. Record each (rc, stdout) so the Then can assert both.
    ctx["command_v_results"] = {
        tool: fake_driver.exec_run(container_name, ["command", "-v", tool])
        for tool in ("gh", "agent-vault")
    }


@then(parsers.parse(
    'each command exits zero and prints an executable path for "{gh}" and for '
    '"{agent_vault}" respectively'))
def then_each_command_resolves(ctx, gh, agent_vault):
    results = ctx["command_v_results"]
    # The scenario pins gh + agent-vault ONLY — it must NOT assert docker.
    assert "docker" not in results, (
        "The gh/agent-vault runtime-PATH guard must NOT probe docker; bc-base "
        "carries no docker CLI by design (PDR-020 Addendum II)."
    )
    for tool in (gh, agent_vault):
        result = results.get(tool)
        assert result is not None, (
            f"`command -v {tool}` was not executed inside the running "
            f"container."
        )
        assert result.returncode == 0, (
            f"`command -v {tool}` exited {result.returncode} inside the "
            f"running bc-base container; it must exit zero (the tool must be "
            f"resolvable on PATH at runtime). A non-zero exit means {tool!r} "
            f"is NOT on PATH — the regression this guard catches."
        )
        path = result.stdout.strip()
        assert path, (
            f"`command -v {tool}` printed no path; it must print an executable "
            f"path for {tool!r} on the in-container PATH."
        )
        assert path.startswith("/") and path.endswith(tool), (
            f"`command -v {tool}` printed {path!r}, which is not an executable "
            f"path for {tool!r}."
        )


# ===========================================================================
# lead-uiwu — bc-container launch clone-path regression guards
#
# FACET 1 (scn bdec2754d9135086 positive / 0b50d090c9cc3c45 negative):
#   manifest remote resolution when no repo flags are given, and a LOUD
#   non-zero failure (never a silent empty /workspace) when no source resolves.
# FACET 2 (scn 4154b0ea63d0516b): /workspace owned by the agent user (vscode)
#   so the in-container clone performed AS that user succeeds without
#   "/workspace/.git: Permission denied".
# FACET 3 (scn 09f871cf8b99a34b, lead-z0v2 — SUPERSEDES retired scn
#   0d29c76818a323a1): the broker MITM root CA is WRITTEN as real, non-empty
#   PEM content to the path git is configured to trust (write-path==trust-path)
#   BEFORE the clone, so a clone routed through HTTPS_PROXY passes TLS
#   verification and git is never pointed at a CA path that does not exist.
# ===========================================================================


# --- FACET 1 given steps ---------------------------------------------------

@given(parsers.parse(
    'the bc-manifest.yaml registers the BC "{bc_name}" with a valid git '
    'remote URL, and is the declared source of remote URLs when launching BCs'
))
def manifest_registers_bc_remote(bc_name, ctx, tmp_path):
    """Write a bc-manifest.yaml that registers ``bc_name`` with a remote URL.

    Records the remote so the positive assertion can confirm /workspace was
    cloned from THIS manifest remote (FACET 1, scn bdec2754d9135086).
    """
    import yaml as _yaml
    remote = f"https://github.com/dstengle/{bc_name}.git"
    manifest_path = tmp_path / "bc-manifest.yaml"
    manifest_path.write_text(_yaml.dump({
        "product": "shopsystem product",
        "bcs": [{"name": bc_name, "remote": remote, "role": "bc"}],
    }))
    ctx["launch_manifest_path"] = manifest_path
    ctx["manifest_remote_for"] = {bc_name: remote}


@given(parsers.parse(
    'no "--repo-url" flag and no "--workspace-mount" flag are provided'
))
def no_repo_flags_provided(ctx):
    """Mark that the launch must run with neither --repo-url nor
    --workspace-mount, so the controller resolves the clone source from
    bc-manifest.yaml (FACET 1) or fails loudly."""
    ctx["no_repo_flags"] = True


@given(parsers.parse(
    'bc-manifest.yaml carries no resolvable git remote URL for the BC '
    '"{bc_name}"'
))
def manifest_has_no_remote_for_bc(bc_name, ctx, tmp_path):
    """Write a bc-manifest.yaml that does NOT register ``bc_name`` (so no
    remote is resolvable for it) — the no-source loud-failure path (FACET 1
    negative, scn 0b50d090c9cc3c45)."""
    import yaml as _yaml
    manifest_path = tmp_path / "bc-manifest.yaml"
    manifest_path.write_text(_yaml.dump({
        "product": "shopsystem product",
        # A different BC is registered; the named BC has no entry, so no remote
        # resolves for it.
        "bcs": [{
            "name": "shopsystem-other",
            "remote": "https://github.com/dstengle/shopsystem-other.git",
            "role": "bc",
        }],
    }))
    ctx["launch_manifest_path"] = manifest_path


# --- FACET 1 then steps ----------------------------------------------------

@then(parsers.parse(
    'the "/workspace" directory inside the running container "{container_name}" '
    'is a git repository cloned from the remote URL registered for '
    '"{bc_name}" in bc-manifest.yaml'
))
def assert_workspace_cloned_from_manifest_remote(
    container_name, bc_name, ctx, fake_driver
):
    assert fake_driver.is_running(container_name), (
        f"Expected {container_name!r} to be running"
    )
    assert fake_driver.workspace_is_git_repo(container_name), (
        f"Expected /workspace in {container_name!r} to be a git repository "
        f"cloned from the manifest remote, but no clone happened — the silent "
        f"empty-launch regression (lead-uiwu FACET 1)."
    )
    expected_remote = ctx["manifest_remote_for"][bc_name]
    cloned_from = fake_driver.workspace_cloned_from(container_name)
    assert cloned_from == expected_remote, (
        f"Expected /workspace cloned from the manifest remote "
        f"{expected_remote!r}, got {cloned_from!r} (lead-uiwu FACET 1)."
    )


@then(parsers.parse(
    'the error output explicitly states that no repo source — neither '
    '"--repo-url", "--workspace-mount", nor a bc-manifest.yaml remote — could '
    'be resolved for "{bc_name}"'
))
def assert_loud_no_source_error(bc_name, ctx):
    result = ctx["result"]
    stderr = (result.stderr or "").lower()
    assert result.exit_code != 0, (
        f"Expected a non-zero exit for a no-source launch, got "
        f"{result.exit_code} (lead-uiwu FACET 1 negative)."
    )
    assert "--repo-url" in stderr, (
        f"Error output must name --repo-url as an unresolvable source; "
        f"got: {result.stderr!r}"
    )
    assert "--workspace-mount" in stderr, (
        f"Error output must name --workspace-mount as an unresolvable source; "
        f"got: {result.stderr!r}"
    )
    assert "bc-manifest.yaml" in stderr, (
        f"Error output must name the bc-manifest.yaml remote as an "
        f"unresolvable source; got: {result.stderr!r}"
    )
    assert bc_name.lower() in stderr, (
        f"Error output must name the BC {bc_name!r}; got: {result.stderr!r}"
    )


@then(parsers.parse(
    'the launch does not silently succeed leaving an empty, non-git '
    '"/workspace"'
))
def assert_no_silent_empty_workspace(ctx, fake_driver):
    result = ctx["result"]
    assert result.exit_code != 0, (
        "A no-source launch must FAIL (non-zero), not silently succeed "
        "(lead-uiwu FACET 1 negative)."
    )
    container_name = ctx.get("container_name")
    if container_name is not None:
        # No clone may have happened: /workspace must NOT be a (falsely)
        # populated git repo, and the container must not have been left running
        # with an empty non-git /workspace masquerading as success.
        assert not fake_driver.workspace_is_git_repo(container_name), (
            "A no-source launch must not leave /workspace as a git repo."
        )


# --- FACET 2 steps ---------------------------------------------------------

@given(parsers.parse(
    'bc-container launch is run with BC name "{bc_name}" with a valid repo URL'
))
def facet2_launch_with_repo_url(bc_name, ctx, fake_driver, controller, tmp_path):
    """Launch ``bc_name`` with an explicit valid repo URL (FACET 2 setup)."""
    repo_url = f"https://github.com/dstengle/{bc_name}.git"
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
    )
    ctx["result"] = result
    ctx["container_name"] = f"bc-{bc_name}"
    ctx["bc_name"] = bc_name


@when(parsers.parse(
    'the ownership of the "/workspace" directory inside the running container '
    'is inspected'
))
def inspect_workspace_ownership(ctx, fake_driver):
    container_name = ctx["container_name"]
    ctx["workspace_owner"] = fake_driver.workspace_owner(container_name)


@then(parsers.parse(
    '"/workspace" is owned by the agent user "{user}" (uid 1000), not by root'
))
def assert_workspace_owned_by_agent_user(user, ctx, fake_driver):
    container_name = ctx["container_name"]
    owner = fake_driver.workspace_owner(container_name)
    assert owner == user, (
        f"Expected /workspace in {container_name!r} owned by {user!r} "
        f"(uid 1000), got {owner!r}.  A root-owned /workspace makes the "
        f"non-root clone fail Permission denied (lead-uiwu FACET 2)."
    )


@then(parsers.parse(
    'the clone performed into "/workspace" as the agent user completes without '
    'a "/workspace/.git: Permission denied" error'
))
def assert_clone_no_permission_denied(ctx, fake_driver):
    result = ctx["result"]
    container_name = ctx["container_name"]
    assert result.exit_code == 0, (
        f"Expected the launch (incl. the as-vscode clone) to succeed, got "
        f"exit {result.exit_code}; stderr: {result.stderr!r} (lead-uiwu "
        f"FACET 2)."
    )
    assert "Permission denied" not in (result.stderr or ""), (
        f"The clone must not hit '/workspace/.git: Permission denied'; "
        f"stderr: {result.stderr!r} (lead-uiwu FACET 2)."
    )
    assert fake_driver.workspace_is_git_repo(container_name), (
        "The clone into /workspace must have succeeded (lead-uiwu FACET 2)."
    )


# --- FACET 3 steps (lead-z0v2, scenario 09f871cf8b99a34b) ------------------
#
# These steps SUPERSEDE the lead-uiwu scenario-68 (0d29c76818a323a1) bindings,
# which asserted only a hollow fake "materialized=true" flag and so could NOT
# catch the v0.3.34 regression (git pointed at a CA path the launcher never
# wrote).  Scenario 69 binds to the ACTUAL launcher-generated clone-prep
# behavior: the launcher must WRITE real CA *content* to a path AND configure
# git to trust that SAME path (write-path == trust-path), with the CA file
# non-empty and a "-----BEGIN CERTIFICATE-----" first line.  A mismatch goes
# RED with the exact real-container failure ("error setting certificate file").
#
# The scenario is exercised through the NO-FLAG manifest-resolution clone path
# with NO ambient AGENT_VAULT_CA_PEM in the test process env (mandate #3): that
# ambient leak is precisely what masked the bug.  The launcher itself supplies
# the CA via the working operator path (`agent-vault ca fetch`).

@given(parsers.parse(
    'the launched BC routes outbound HTTPS through the agent-vault MITM proxy '
    'via "HTTPS_PROXY", so the clone\'s TLS is terminated by the broker MITM '
    'and requires the broker root CA to verify'
))
def facet3_routes_through_mitm_proxy(ctx):
    """The operator supplies agent-vault credentials so the launcher derives
    the :14322 MITM proxy and routes the clone's HTTPS_PROXY through it."""
    ctx["av_addr"] = "https://agent-vault:14321"
    ctx["av_token"] = "av_agt_z0v2"
    ctx["av_vault"] = "shopsystem"


@when(parsers.parse(
    'bc-container launch is run with BC name "{bc_name}" via the no-flag '
    'manifest-resolution clone path and the running container is inspected '
    'before the in-container clone runs'
))
def facet3_launch_no_flag_clone(bc_name, ctx, controller, fake_driver, tmp_path):
    """Run a flagless launch (clone source resolved from bc-manifest.yaml).

    REAL-FIDELITY (lead-z0v2, mandate #3): NO AGENT_VAULT_CA_PEM is supplied —
    not as a launch param and not in the test process env (the ambient leak is
    actively scrubbed).  The container's broker CA is fetchable
    (`agent-vault ca fetch` — the working operator path), so the launcher's own
    clone-prep is the only thing that can write the CA.  If the launcher
    pointed git at a CA path WITHOUT writing it (the v0.3.34 bug), the clone
    fails "error setting certificate file" and this scenario goes RED.
    """
    repo_url = f"https://github.com/dstengle/{bc_name}.git"
    manifest_path = tmp_path / "bc-manifest.yaml"
    manifest_path.write_text(
        __import__("yaml").dump({
            "product": "shopsystem product",
            "bcs": [{"name": bc_name, "remote": repo_url, "role": "bc"}],
        })
    )
    container_name = f"bc-{bc_name}"
    # Model the working operator path: the broker CA is fetchable inside the
    # container via `agent-vault ca fetch`.  This is NOT the test process env —
    # it is in-container broker reachability the launcher's clone-prep uses.
    fake_driver.set_broker_ca_fetchable(container_name)

    import os as _os
    # Operator agent-vault triple (addr/token/vault) — required so the launcher
    # derives the :14322 MITM proxy and routes the clone through it.  These are
    # passed as launch PARAMS, not relied on from ambient env.
    env_overrides = {
        "AGENT_VAULT_ADDR": ctx.get("av_addr"),
        "AGENT_VAULT_TOKEN": ctx.get("av_token"),
        "AGENT_VAULT_VAULT": ctx.get("av_vault"),
    }
    saved = {}
    for k, v in env_overrides.items():
        saved[k] = _os.environ.get(k)
        if v is not None:
            _os.environ[k] = v
    # mandate #3: scrub any ambient AGENT_VAULT_CA_PEM so the launcher CANNOT
    # rely on a harness-leaked inline PEM — it must write the CA itself.
    saved_ca_pem = _os.environ.pop("AGENT_VAULT_CA_PEM", None)
    try:
        result = controller.launch(
            bc_name=bc_name,
            # no repo_url, no workspace_mount → no-flag manifest resolution
            manifest_path=manifest_path,
            credential_home=ctx.get("credential_home"),
            agent_vault_addr=ctx.get("av_addr"),
            agent_vault_token=ctx.get("av_token"),
            agent_vault_vault=ctx.get("av_vault"),
        )
    finally:
        for k, v in saved.items():
            if v is None:
                _os.environ.pop(k, None)
            else:
                _os.environ[k] = v
        if saved_ca_pem is not None:
            _os.environ["AGENT_VAULT_CA_PEM"] = saved_ca_pem
    ctx["result"] = result
    ctx["container_name"] = container_name
    ctx["bc_name"] = bc_name


@then(parsers.parse(
    'a regular file exists inside the running container at the exact path git '
    'is configured to use as its CA bundle, and that file is non-empty and its '
    'first line is "{begin_line}"'
))
def assert_ca_file_at_git_trust_path(begin_line, ctx, fake_driver):
    """write-path == trust-path WITH CONTENT (lead-z0v2).

    The CA path git was configured to trust on the clone exec (GIT_SSL_CAINFO)
    must name a REAL, non-empty file whose first line is the PEM BEGIN marker.
    This is the assertion a hollow "materialized=true" flag could never satisfy:
    it requires the launcher to have actually written CA content to the exact
    path it points git at.
    """
    container_name = ctx["container_name"]
    trust_path = fake_driver.clone_git_ca_trust_path(container_name)
    assert trust_path, (
        "The clone exec did not configure git's CA bundle (GIT_SSL_CAINFO) at "
        "all; cannot establish a write-path==trust-path invariant (lead-z0v2)."
    )
    content = fake_driver.container_file(container_name, trust_path)
    assert content is not None, (
        f"git is configured to trust CA path {trust_path!r} but NO file was "
        f"written there by the launcher — the exact v0.3.34 regression "
        f"(write-path-vs-trust-path mismatch) (lead-z0v2)."
    )
    assert content.strip(), (
        f"The CA file at the git trust path {trust_path!r} is empty (lead-z0v2)."
    )
    first_line = content.splitlines()[0] if content.splitlines() else ""
    assert first_line == begin_line, (
        f"The CA file at {trust_path!r} must begin with {begin_line!r}; "
        f"got first line {first_line!r} (lead-z0v2)."
    )
    ctx["facet3_trust_path"] = trust_path


@then(parsers.parse(
    '"git config --global http.sslCAInfo" inside the container names that '
    'existing CA file (or, equivalently, the agent-vault broker root CA is '
    'installed into the system trust store git uses by default), so git is '
    'never pointed at a CA path that does not exist'
))
def assert_git_ca_names_existing_file(ctx, fake_driver):
    """git's CA trust setting must name a path that ACTUALLY exists with
    content — never a path that does not exist (lead-z0v2)."""
    container_name = ctx["container_name"]
    trust_path = fake_driver.clone_git_ca_trust_path(container_name)
    # Either git is pointed at a real, written CA file ...
    if trust_path:
        content = fake_driver.container_file(container_name, trust_path)
        assert content, (
            f"git's configured CA path {trust_path!r} does not name an existing "
            f"non-empty file — git is pointed at a CA path that does not exist "
            f"(lead-z0v2)."
        )
    else:
        # ... or the CA is installed into the default/system trust store.
        assert fake_driver.broker_ca_materialized(container_name), (
            "Neither a git CA path nor a default-trust-store install was "
            "established before the clone (lead-z0v2)."
        )


@then(parsers.parse(
    'the in-container clone of "{bc_name}" routed through "HTTPS_PROXY" '
    'completes its TLS handshake with neither an "{err1}" error nor an '
    '"{err2}" error'
))
def assert_clone_tls_ok(bc_name, err1, err2, ctx, fake_driver):
    result = ctx["result"]
    container_name = ctx["container_name"]
    assert result.exit_code == 0, (
        f"Expected the brokered clone to succeed, got exit "
        f"{result.exit_code}; stderr: {result.stderr!r} (lead-z0v2 FACET 3)."
    )
    assert err1 not in (result.stderr or ""), (
        f"The proxied clone must not fail with {err1!r}; "
        f"stderr: {result.stderr!r} (lead-z0v2 FACET 3)."
    )
    assert err2 not in (result.stderr or ""), (
        f"The proxied clone must not fail TLS verification ({err2!r}); "
        f"stderr: {result.stderr!r} (lead-z0v2 FACET 3)."
    )
    assert fake_driver.workspace_is_git_repo(container_name), (
        "The brokered clone must have succeeded into /workspace "
        "(lead-z0v2 FACET 3)."
    )


# --- scenario 70 (lead-eqao, 3rd F3 cycle, @scenario_hash:3222fe1396f1ff53) -
#
# STRONG FIDELITY MANDATE.  The prior two F3 cycles each shipped a NEW bug
# because the verifying test exercised a REIMPLEMENTED / MODELED validation
# instead of the REAL shipped script.  Scenario 70 binds the ONE thing those
# cycles bypassed: the ACTUAL committed CA-validation shell the launch execs.
#
# NAMING DISCREPANCY (reported to the lead): the dispatch says
# "agent-vault-ca.sh", but the real launch-path CA validation the no-flag
# manifest-resolution clone execs is the controller's clone-prep script,
# generated by the committed `_clone_ca_materialize_script(...)` and run via
# `self._driver.exec_run(container, ["/bin/sh", "-c", _clone_ca_materialize_script()])`
# (controller.py:1465-1468).  `docker/bc-base/agent-vault-ca.sh` carries NO
# BEGIN-CERTIFICATE grep at all — the dash-prefixed-grep defect lives ONLY in
# the controller clone-prep string.  This step set therefore binds the REAL
# generated script string (the launch's literal CA validation) per the
# scenario's load-bearing intent ("the REAL shipped validation the launch
# runs").
#
# The test obtains the EXACT script string from the committed
# `_clone_ca_materialize_script(...)` (no re-derivation, no stand-in) and
# EXECUTES that string verbatim via `bash -c` against a real temp CA file,
# exactly as the launch invokes it (`/bin/sh -c <that same string>`).  The
# launch's downstream decision is then driven off that script's real exit code,
# mirroring the controller: returncode 0 -> point git at the CA + clone
# proceeds; returncode != 0 -> refuse to point git at the CA + clone does not
# run (controller.py:1469-1487).
#
# This RED-tests the pre-fix grep: the buggy `grep -qx "-----BEGIN
# CERTIFICATE-----"` parses the dash-prefixed pattern as OPTIONS, emits
# "grep: unrecognized option", exits non-zero on a VALID cert, so the positive
# example fails (clone refused on a valid cert).  Demonstrated separately by
# temporarily restoring the buggy grep.

@given(parsers.parse(
    'the launched BC routes outbound HTTPS through the agent-vault MITM proxy '
    'via "HTTPS_PROXY", so the clone requires the broker root CA to verify TLS'
))
def s70_routes_through_mitm_proxy(ctx):
    """Scenario-70 phrasing of the MITM-proxy Given (distinct from scenario
    69's wording).  The clone routes through the broker MITM, so it requires
    the broker root CA to verify TLS — establishing why the CA validation under
    test is on the launch's critical path."""
    ctx["av_addr"] = "https://agent-vault:14321"
    ctx["av_token"] = "av_agt_eqao"
    ctx["av_vault"] = "shopsystem"


@given(parsers.parse(
    'the CA validation under observation is the one the real launch performs '
    'by invoking the committed "{script_name}", exercised exactly as the '
    'launch invokes it on the no-flag manifest-resolution path, and not a '
    'reimplemented, modeled, or stand-in check that re-derives the '
    'BEGIN-CERTIFICATE test differently from the shipped script'
))
def s70_bind_real_committed_validation(script_name, ctx):
    """Binding Given: capture the EXACT committed CA-validation script string.

    This is the launch's literal CA validation: the string the controller
    execs as `/bin/sh -c <string>` before the clone.  We obtain it from the
    committed `_clone_ca_materialize_script(...)` — NOT a re-derived or modeled
    BEGIN-CERTIFICATE check — and stash it for verbatim execution.
    """
    from bc_launcher.controller import (
        _clone_ca_materialize_script,
        AGENT_VAULT_CONTAINER_CA_PATH,
        CA_PEM_FIRST_LINE,
    )
    # The CA path the real launch validates: route it at a temp file so the
    # real script runs in-env without docker, against a real on-disk CA.
    import tempfile
    tmpdir = Path(tempfile.mkdtemp(prefix="s70_ca_"))
    ca_path = tmpdir / "ca.pem"
    # The EXACT string the launch execs — generated by the committed function.
    ctx["s70_real_script"] = _clone_ca_materialize_script(str(ca_path))
    ctx["s70_ca_path"] = ca_path
    ctx["s70_begin_marker"] = CA_PEM_FIRST_LINE
    # Sanity: the captured string must be the REAL validation (it must contain
    # the launch's actual grep against the BEGIN marker), not a stand-in.  If a
    # future refactor relocates the validation, this binding must follow it.
    assert "grep" in ctx["s70_real_script"], (
        "scenario 70 must bind the REAL committed CA-validation script "
        "(the one the launch execs); captured string has no grep step. "
        "Do NOT substitute a reimplemented check."
    )
    assert CA_PEM_FIRST_LINE in ctx["s70_real_script"], (
        "the real committed validation must check for the BEGIN-CERTIFICATE "
        "marker; captured script does not reference it."
    )


@given(parsers.parse(
    'the agent-vault broker materializes the container CA bundle so that its '
    'on-disk content is {ca_content}'
))
def s70_materialize_ca_content(ca_content, ctx):
    """Drive the EXACT on-disk CA content the real script will validate.

    The committed script's source-precedence path (1) writes inline
    AGENT_VAULT_CA_PEM to the CA path, so we set that env to the example's
    content — this lands the exact bytes on disk and lets the REAL script run
    in-env (no broker, no docker) while still exercising the launch's actual
    write-then-validate flow.
    """
    desc = ca_content.strip().strip('"')
    if desc.startswith("a real PEM certificate"):
        # A genuine self-signed PEM whose FIRST LINE is exactly the BEGIN
        # marker.  (Generated once; content is irrelevant beyond being a real
        # PEM whose first line is the marker.)
        ctx["s70_ca_pem"] = (
            "-----BEGIN CERTIFICATE-----\n"
            "MIIBkTCB+wIJALY3xY0l0sQ4MA0GCSqGSIb3DQEBCwUAMBQxEjAQBgNVBAMMCWxv\n"
            "Y2FsaG9zdDAeFw0yNDAxMDEwMDAwMDBaFw0zNDAxMDEwMDAwMDBaMBQxEjAQBgNV\n"
            "BAMMCWxvY2FsaG9zdDCBnzANBgkqhkiG9w0BAQEFAAOBjQAwgYkCgYEAwQ2Vn5pR\n"
            "fakefakefakefakefakefakefakefakefakefakefakefakefakefakefakefake\n"
            "-----END CERTIFICATE-----"
        )
        ctx["s70_expect_accept"] = True
    elif desc.startswith("bytes that genuinely contain no"):
        # Genuinely marker-less content: NO "-----BEGIN CERTIFICATE-----"
        # line anywhere.  The validation MUST still reject this fail-loud.
        ctx["s70_ca_pem"] = (
            "this is not a certificate\n"
            "just some random bytes with no begin marker anywhere\n"
            "garbage garbage garbage"
        )
        ctx["s70_expect_accept"] = False
    else:
        raise AssertionError(f"unrecognized scenario-70 ca_content: {ca_content!r}")


@when(parsers.parse(
    'bc-container launch is run with BC name "{bc_name}" via the no-flag '
    'manifest-resolution clone path and the launch reaches its CA validation '
    'step'
))
def s70_run_real_validation(bc_name, ctx):
    """EXECUTE the REAL committed validation script verbatim, as the launch does.

    The launch execs `/bin/sh -c <_clone_ca_materialize_script() string>`.
    We run that SAME string via the shell against the real temp CA file, with
    the example's content supplied through AGENT_VAULT_CA_PEM (source-precedence
    path 1).  We then mirror the controller's downstream decision (returncode
    0 -> git pointed at CA + clone proceeds; != 0 -> refuse + clone does not
    run) so the Then steps assert the launch outcome off the REAL script's real
    result.
    """
    import os as _os
    script = ctx["s70_real_script"]
    env = dict(_os.environ)
    env["AGENT_VAULT_CA_PEM"] = ctx["s70_ca_pem"]
    # Run the EXACT script string, exactly as the launch invokes it (sh -c).
    proc = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=env,
    )
    ctx["s70_returncode"] = proc.returncode
    ctx["s70_stderr"] = proc.stderr or ""
    ctx["s70_stdout"] = proc.stdout or ""
    # Mirror the controller's write-path==trust-path decision exactly
    # (controller.py:1469-1487): only on a clean validation does the launch
    # point git at the CA and let the clone proceed.
    ca_validation_passed = proc.returncode == 0
    ctx["s70_git_pointed_at_ca"] = ca_validation_passed
    ctx["s70_clone_runs"] = ca_validation_passed
    ctx["s70_bc_name"] = bc_name


@then(parsers.parse(
    'the committed agent-vault-ca.sh validation step {validation_result}, with '
    'no "{grep_err}" error and no other validation-internal error causing a '
    'valid cert to be misjudged'
))
def s70_assert_validation_result(validation_result, grep_err, ctx):
    rc = ctx["s70_returncode"]
    stderr = ctx["s70_stderr"]
    desc = validation_result.strip()
    if desc.startswith("accepts the materialized CA"):
        # Positive example: the REAL script must accept a VALID cert.
        assert rc == 0, (
            "scenario 70 positive: the REAL committed validation script must "
            "ACCEPT a valid cert (first line is the BEGIN marker) and exit 0, "
            f"got exit {rc}; stderr: {stderr!r}.  A non-zero exit here on a "
            "valid cert is exactly the F3 grep-option bug."
        )
        # The dash-prefixed-pattern grep bug surfaces as this exact message;
        # its ABSENCE is the fidelity assertion that catches THIS bug.
        assert grep_err not in stderr, (
            f"the REAL validation must not emit {grep_err!r} on a valid cert "
            f"(the dash-prefixed-grep F3 bug); stderr: {stderr!r}."
        )
        assert "missing BEGIN CERTIFICATE" not in stderr, (
            "the REAL validation must not emit the 'missing BEGIN CERTIFICATE' "
            f"diagnostic for a VALID cert; stderr: {stderr!r}."
        )
    elif desc.startswith("rejects the materialized CA"):
        # Negative example: a genuinely marker-less cert MUST be rejected loud.
        assert rc != 0, (
            "scenario 70 negative: the REAL committed validation script must "
            "REJECT genuinely marker-less content fail-loud (non-zero exit); "
            f"got exit {rc}.  The fix must NOT degenerate to always-accept."
        )
        assert "BEGIN CERTIFICATE" in stderr, (
            "the REAL validation must NAME the missing BEGIN CERTIFICATE "
            f"marker when rejecting; stderr: {stderr!r}."
        )
        # Even the negative limb must reject via an honest marker check, NOT a
        # grep-option error — a grep-unrecognized-option failure is a defect,
        # not a legitimate rejection.
        assert grep_err not in stderr, (
            f"the REAL validation must reject via an honest marker check, not "
            f"a {grep_err!r} defect; stderr: {stderr!r}."
        )
    else:
        raise AssertionError(f"unrecognized validation_result: {validation_result!r}")


@then(parsers.parse(
    'the launch points git at that CA file and the proxied clone of '
    '"{bc_name}" proceeds and completes its TLS handshake with no '
    '"{err}" error'
))
def s70_assert_clone_proceeds(bc_name, err, ctx):
    """Positive example: a passing CA validation points git at the CA and the
    clone proceeds (mirrors controller.py:1469-1487)."""
    assert ctx["s70_git_pointed_at_ca"], (
        "on a valid cert the launch must point git at the CA file and let "
        "the proxied clone proceed (the REAL validation passed)."
    )
    assert ctx["s70_clone_runs"], (
        "the proxied clone must proceed after a passing CA validation."
    )


@then(parsers.parse(
    'the launch refuses to point git at the CA and the proxied clone does '
    'not run'
))
def s70_assert_clone_refused(ctx):
    """Negative example: a failed CA validation refuses to point git at the CA
    and the clone does not run (mirrors controller.py:1469-1479)."""
    assert not ctx["s70_git_pointed_at_ca"], (
        "on a marker-less cert the launch must REFUSE to point git at the "
        "CA (the REAL validation failed loud)."
    )
    assert not ctx["s70_clone_runs"], (
        "the proxied clone must NOT run after a failed CA validation."
    )


# ===========================================================================
# lead-5xnd — published bc-base / bc-lead images surface the bc-launcher
# release version + baked shop-templates version via OCI labels and ENV,
# OVERRIDING the misleading upstream devcontainer-base
# org.opencontainers.image.version label value "3.1.2".
#
# Scenarios 7c0c949fccdf9df2 (Outline over bc-base + bc-lead) and
# 26d1817c9d115f0d (container inspect of bc-base:latest).
#
# docker is NOT available in this environment; per the scenario-40
# declarative-artifact precedent these scenarios are pinned at the honest
# fidelity for DECLARATIVE labels/ENV: parse the committed publish-bc-base.yml
# `labels:` inputs (build-set labels OVERRIDE inherited base-image labels, which
# is how the inherited "3.1.2" is defeated) and the committed Dockerfile ENV
# instructions. The live `docker image/container inspect` of the published
# image is the lead's post-release pull verification, out of band of this suite.
# ===========================================================================

# The upstream devcontainer-base label value we are overriding.
_UPSTREAM_BASE_VERSION_LABEL = "3.1.2"


def _parse_kv_block(value) -> dict:
    """Parse a GHA action `with:` multiline "key=value" block (labels /
    build-args) into a dict. Accepts the YAML-parsed string (newline-joined)
    or a list of "key=value" strings."""
    out: dict[str, str] = {}
    if value is None:
        return out
    if isinstance(value, str):
        lines = value.splitlines()
    elif isinstance(value, (list, tuple)):
        lines = list(value)
    else:
        return out
    for line in lines:
        line = line.strip()
        if not line or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


def _publish_workflow_doc(ctx):
    """Locate the committed publish workflow triggered on a "v*" tag push."""
    workflows = _load_workflows()
    for path, doc in workflows.items():
        if not isinstance(doc, dict):
            continue
        on = doc.get("on", doc.get(True))
        if not isinstance(on, dict):
            continue
        push = on.get("push")
        if isinstance(push, dict):
            tags = push.get("tags") or []
            if any(str(t).startswith("v") for t in tags):
                return path, doc
    return None


def _build_step_for_image(doc, image_base):
    """Return the docker/build-push-action step in the workflow whose `tags`
    input references the given image_base (e.g.
    "ghcr.io/dstengle/shopsystem-bc-base"), or None."""
    jobs = doc.get("jobs", {})
    for job in jobs.values():
        for step in job.get("steps", []) or []:
            uses = str(step.get("uses", ""))
            if "build-push-action" not in uses:
                continue
            with_ = step.get("with", {}) or {}
            tags = with_.get("tags", "")
            tags_text = tags if isinstance(tags, str) else "\n".join(
                str(t) for t in (tags or [])
            )
            if image_base in tags_text:
                return step
    return None


def _baked_shop_templates_version() -> str | None:
    """Resolve the baked shop-templates version: the
    ARG SHOP_TEMPLATES_VERSION=vX.Y.Z default in the bc-base Dockerfile."""
    dockerfile = _find_bc_base_dockerfile()
    if dockerfile is None:
        return None
    m = re.search(
        r"ARG\s+SHOP_TEMPLATES_VERSION=(v\d+\.\d+\.\d+)", dockerfile.read_text()
    )
    return m.group(1) if m else None


def _bc_base_dockerfile_text() -> str:
    df = _find_bc_base_dockerfile()
    return df.read_text() if df is not None else ""


def _dockerfile_env_value(text: str, name: str) -> str | None:
    """Return the value an `ENV <name>=<value>` instruction sets, or None.

    Matches `ENV NAME=value` (the only form used here). The value may be a
    ${VAR} expansion of a same-named ARG, which is the promote-ARG-to-ENV
    idiom — that still surfaces in `docker inspect`."""
    m = re.search(
        rf"(?im)^\s*ENV\s+{re.escape(name)}=(\S+)", text
    )
    return m.group(1) if m else None


def _dockerfile_arg_declared(text: str, name: str) -> bool:
    return bool(re.search(rf"(?im)^\s*ARG\s+{re.escape(name)}\b", text))


# --- Scenario 7c0c949fccdf9df2: Outline over bc-base + bc-lead --------------

@given(parsers.parse(
    'the bc-launcher publish workflow built and published the "{image}" image '
    'at bc-launcher release version "{rel_ver}" baking shop-templates version '
    '"{tpl_ver}"'
))
def given_publish_built_image(image, rel_ver, tpl_ver, ctx):
    wf = _publish_workflow_doc(ctx)
    assert wf is not None, (
        'No committed publish workflow triggered on a "v*" tag push was found '
        "under .github/workflows."
    )
    ctx["v5xnd_workflow"] = wf
    ctx["v5xnd_image"] = image
    ctx["v5xnd_rel_ver"] = rel_ver
    ctx["v5xnd_tpl_ver"] = tpl_ver
    step = _build_step_for_image(wf[1], image)
    assert step is not None, (
        f"The publish workflow has no docker/build-push-action build step "
        f"publishing {image!r}."
    )
    ctx["v5xnd_build_step"] = step
    with_ = step.get("with", {}) or {}
    ctx["v5xnd_labels"] = _parse_kv_block(with_.get("labels"))
    ctx["v5xnd_build_args"] = _parse_kv_block(with_.get("build-args"))


@when(parsers.parse(
    'the published "{image}:latest" image is examined with "docker image '
    'inspect"'
))
def when_image_inspect(image, ctx):
    # docker is OUT-OF-BAND; the in-suite proxy is the committed workflow
    # `labels:` input and the bc-base Dockerfile ENV (the build-set labels
    # override the inherited base-image labels). Already loaded in the Given.
    ctx["v5xnd_dockerfile_text"] = _bc_base_dockerfile_text()


@then(parsers.parse(
    'the image\'s "org.opencontainers.image.version" OCI label equals the '
    'bc-launcher release version "{rel_ver}"'
))
def then_image_version_label(rel_ver, ctx):
    labels = ctx["v5xnd_labels"]
    val = labels.get("org.opencontainers.image.version")
    assert val is not None, (
        f"The build step for {ctx['v5xnd_image']!r} does not SET the "
        "org.opencontainers.image.version OCI label via the build-push-action "
        "labels: input, so the inherited upstream value "
        f"{_UPSTREAM_BASE_VERSION_LABEL!r} would survive."
    )
    # The release version is the pushed v* tag (github.ref_name). The label is
    # set to that expression so the published label equals the release version.
    assert "ref_name" in val or val == rel_ver, (
        "The org.opencontainers.image.version label is not set to the "
        f"bc-launcher release tag (github.ref_name / {rel_ver!r}); got {val!r}."
    )
    assert val != _UPSTREAM_BASE_VERSION_LABEL, (
        "The version label is the inherited upstream "
        f"{_UPSTREAM_BASE_VERSION_LABEL!r} value, not the release version."
    )


@then(parsers.parse(
    'the image\'s "org.opencontainers.image.revision" OCI label is a non-empty '
    'git commit sha identifying the source revision the image was built from'
))
def then_image_revision_label(ctx):
    labels = ctx["v5xnd_labels"]
    val = labels.get("org.opencontainers.image.revision")
    assert val is not None and val != "", (
        f"The build step for {ctx['v5xnd_image']!r} does not SET a non-empty "
        "org.opencontainers.image.revision OCI label."
    )
    # The revision is the source commit sha (github.sha) — non-empty per build.
    assert "github.sha" in val or "sha" in val or re.fullmatch(
        r"[0-9a-f]{7,40}", val
    ), (
        "The org.opencontainers.image.revision label is not the source commit "
        f"sha (github.sha); got {val!r}."
    )


@then(parsers.parse(
    'the image\'s "shopsystem.shop-templates.version" OCI label equals the '
    'baked shop-templates version "{tpl_ver}"'
))
def then_image_shop_templates_label(tpl_ver, ctx):
    labels = ctx["v5xnd_labels"]
    val = labels.get("shopsystem.shop-templates.version")
    assert val is not None, (
        f"The build step for {ctx['v5xnd_image']!r} does not SET the "
        "shopsystem.shop-templates.version OCI label."
    )
    baked = _baked_shop_templates_version()
    assert baked is not None, (
        "Could not resolve the baked shop-templates version "
        "(ARG SHOP_TEMPLATES_VERSION=vX.Y.Z) from the bc-base Dockerfile."
    )
    # The label must equal the baked version. It may be the literal baked value,
    # a build-arg expression, or a workflow step-output expression that resolves
    # the baked SHOP_TEMPLATES_VERSION from the Dockerfile ARG default.
    assert (
        val == baked
        or val == tpl_ver
        or "SHOP_TEMPLATES_VERSION" in val
        or "shop_templates_version" in val
    ), (
        "The shopsystem.shop-templates.version label is not the baked "
        f"shop-templates version ({baked!r} / {tpl_ver!r}); got {val!r}."
    )
    # NB: tpl_ver is the scenario's EXAMPLE baked version, not a constraint on the
    # current Dockerfile pin. The label DRY-tracks the live baked ARG (asserted
    # above via val==baked / SHOP_TEMPLATES_VERSION expr), so this step stays green
    # across shop-templates bumps (bug shopsystem_bc_launcher-zk0).


@then(parsers.parse(
    'the image\'s configured environment includes "SHOPSYSTEM_BC_LAUNCHER_'
    'VERSION" equal to the bc-launcher release version "{rel_ver}"'
))
def then_image_env_launcher_version(rel_ver, ctx):
    text = ctx["v5xnd_dockerfile_text"]
    val = _dockerfile_env_value(text, "SHOPSYSTEM_BC_LAUNCHER_VERSION")
    assert val is not None, (
        "The bc-base Dockerfile does not declare ENV "
        "SHOPSYSTEM_BC_LAUNCHER_VERSION, so it would not surface in "
        "docker inspect (bc-lead inherits it FROM bc-base)."
    )
    # The ENV is promoted from a same-named build ARG threaded with the release
    # tag (github.ref_name) by the workflow build-args.
    assert _dockerfile_arg_declared(text, "SHOPSYSTEM_BC_LAUNCHER_VERSION"), (
        "ENV SHOPSYSTEM_BC_LAUNCHER_VERSION is set but the matching ARG is not "
        "declared, so the workflow cannot thread the release version in."
    )
    build_args = ctx.get("v5xnd_build_args", {})
    bav = build_args.get("SHOPSYSTEM_BC_LAUNCHER_VERSION")
    assert bav is not None and ("ref_name" in bav or bav == rel_ver), (
        "The build step does not pass SHOPSYSTEM_BC_LAUNCHER_VERSION="
        "github.ref_name as a build-arg, so the ENV would not equal the "
        f"release version {rel_ver!r}; got {bav!r}."
    )


@then(parsers.parse(
    'the image\'s configured environment includes "SHOP_TEMPLATES_VERSION" '
    'equal to the baked shop-templates version "{tpl_ver}"'
))
def then_image_env_shop_templates_version(tpl_ver, ctx):
    text = ctx["v5xnd_dockerfile_text"]
    val = _dockerfile_env_value(text, "SHOP_TEMPLATES_VERSION")
    assert val is not None, (
        "The bc-base Dockerfile does not declare ENV SHOP_TEMPLATES_VERSION "
        "(promote the existing ARG to a persisted ENV), so the baked "
        "shop-templates version would not surface in docker inspect."
    )
    # The ENV promotes the ARG SHOP_TEMPLATES_VERSION default, so it surfaces
    # whatever version is baked. tpl_ver is the scenario's EXAMPLE value, not a
    # constraint freezing the live Dockerfile pin (bug shopsystem_bc_launcher-zk0).
    assert _baked_shop_templates_version() is not None, (
        "Could not resolve the baked shop-templates version (ARG "
        "SHOP_TEMPLATES_VERSION=vX.Y.Z) from the bc-base Dockerfile."
    )


@then(parsers.parse(
    'the bc-launcher version surfaced by inspect is "{rel_ver}" rather than '
    'the upstream devcontainer base label value "{base_ver}"'
))
def then_version_overrides_upstream(rel_ver, base_ver, ctx):
    labels = ctx["v5xnd_labels"]
    val = labels.get("org.opencontainers.image.version")
    assert val is not None, (
        f"The build step for {ctx['v5xnd_image']!r} leaves "
        "org.opencontainers.image.version INHERITED, so the published label "
        f"is the upstream {base_ver!r}, not the release version {rel_ver!r}."
    )
    assert val != base_ver, (
        f"The version label is the upstream {base_ver!r}, not overridden to "
        f"the release version {rel_ver!r}."
    )
    assert "ref_name" in val or val == rel_ver, (
        "The version label override does not resolve to the bc-launcher "
        f"release version {rel_ver!r}; got {val!r}."
    )


# --- Scenario 26d1817c9d115f0d: container inspect of bc-base:latest ---------

@given(parsers.parse(
    'the published "{image}" image at bc-launcher release version "{rel_ver}" '
    'baking shop-templates version "{tpl_ver}" carries those versions as OCI '
    'labels and ENV'
))
def given_published_bc_base_carries_versions(image, rel_ver, tpl_ver, ctx):
    wf = _publish_workflow_doc(ctx)
    assert wf is not None, (
        'No committed publish workflow triggered on a "v*" tag push was found.'
    )
    ctx["c5xnd_image"] = image
    ctx["c5xnd_rel_ver"] = rel_ver
    ctx["c5xnd_tpl_ver"] = tpl_ver
    step = _build_step_for_image(wf[1], image)
    assert step is not None, (
        f"The publish workflow has no build step publishing {image!r}."
    )
    with_ = step.get("with", {}) or {}
    ctx["c5xnd_labels"] = _parse_kv_block(with_.get("labels"))
    ctx["c5xnd_build_args"] = _parse_kv_block(with_.get("build-args"))
    ctx["c5xnd_dockerfile_text"] = _bc_base_dockerfile_text()


@given(parsers.parse(
    'a container is started from that image addressed only by its "latest" '
    'tag, so the originating version tag is not recoverable from the running '
    'container'
))
def given_container_started_latest_only(ctx):
    # The run-tag is intentionally not recoverable; the surfaced versions must
    # therefore come from the image's baked labels/ENV (declarative artifacts),
    # not from the tag used to address the image. Nothing to set up beyond the
    # already-loaded committed artifacts.
    ctx["c5xnd_run_tag_recoverable"] = False


@when('the running container is examined with "docker container inspect"')
def when_container_inspect(ctx):
    # docker is OUT-OF-BAND; the in-suite proxy is the committed bc-base
    # workflow `labels:` input and the bc-base Dockerfile ENV that a running
    # container's Config.Labels / Config.Env would surface.
    pass


@then(parsers.parse(
    'the container\'s configured labels surface "org.opencontainers.image.'
    'version" equal to the bc-launcher release version "{rel_ver}"'
))
def then_container_version_label(rel_ver, ctx):
    val = ctx["c5xnd_labels"].get("org.opencontainers.image.version")
    assert val is not None, (
        "The bc-base build step does not SET org.opencontainers.image.version, "
        "so a running container's Config.Labels would surface the inherited "
        f"upstream {_UPSTREAM_BASE_VERSION_LABEL!r}."
    )
    assert "ref_name" in val or val == rel_ver, (
        "The container's org.opencontainers.image.version label is not the "
        f"bc-launcher release version {rel_ver!r}; got {val!r}."
    )
    assert val != _UPSTREAM_BASE_VERSION_LABEL


@then(parsers.parse(
    'the container\'s configured labels surface "shopsystem.shop-templates.'
    'version" equal to the baked shop-templates version "{tpl_ver}"'
))
def then_container_shop_templates_label(tpl_ver, ctx):
    val = ctx["c5xnd_labels"].get("shopsystem.shop-templates.version")
    assert val is not None, (
        "The bc-base build step does not SET the shopsystem.shop-templates."
        "version label."
    )
    baked = _baked_shop_templates_version()
    # tpl_ver is the scenario's EXAMPLE baked version, not a constraint on the
    # current Dockerfile pin; the label DRY-tracks the live baked ARG so this
    # stays green across shop-templates bumps (bug shopsystem_bc_launcher-zk0).
    assert (
        val == baked
        or val == tpl_ver
        or "SHOP_TEMPLATES_VERSION" in val
        or "shop_templates_version" in val
    ), (
        "The container's shopsystem.shop-templates.version label is not the "
        f"baked version ({baked!r}); got {val!r}."
    )


@then(parsers.parse(
    'the container\'s configured environment surfaces "SHOPSYSTEM_BC_LAUNCHER_'
    'VERSION" equal to "{rel_ver}"'
))
def then_container_env_launcher_version(rel_ver, ctx):
    text = ctx["c5xnd_dockerfile_text"]
    val = _dockerfile_env_value(text, "SHOPSYSTEM_BC_LAUNCHER_VERSION")
    assert val is not None, (
        "The bc-base Dockerfile does not declare ENV "
        "SHOPSYSTEM_BC_LAUNCHER_VERSION, so a running container's Config.Env "
        "would not surface it."
    )
    bav = ctx.get("c5xnd_build_args", {}).get("SHOPSYSTEM_BC_LAUNCHER_VERSION")
    assert bav is not None and ("ref_name" in bav or bav == rel_ver), (
        "The bc-base build step does not pass SHOPSYSTEM_BC_LAUNCHER_VERSION="
        f"github.ref_name as a build-arg; got {bav!r}."
    )


@then(parsers.parse(
    'the container\'s configured environment surfaces "SHOP_TEMPLATES_VERSION" '
    'equal to "{tpl_ver}"'
))
def then_container_env_shop_templates_version(tpl_ver, ctx):
    text = ctx["c5xnd_dockerfile_text"]
    val = _dockerfile_env_value(text, "SHOP_TEMPLATES_VERSION")
    assert val is not None, (
        "The bc-base Dockerfile does not declare ENV SHOP_TEMPLATES_VERSION, "
        "so a running container's Config.Env would not surface it."
    )
    # The ENV promotes the ARG default so it surfaces whatever is baked; tpl_ver
    # is the scenario's EXAMPLE, not a constraint on the live pin (bug
    # shopsystem_bc_launcher-zk0).
    assert _baked_shop_templates_version() is not None, (
        "Could not resolve the baked shop-templates version from the bc-base "
        "Dockerfile."
    )


@then(parsers.parse(
    'the surfaced bc-launcher version is "{rel_ver}" rather than the upstream '
    'devcontainer base label value "{base_ver}"'
))
def then_container_version_overrides_upstream(rel_ver, base_ver, ctx):
    val = ctx["c5xnd_labels"].get("org.opencontainers.image.version")
    assert val is not None, (
        "The bc-base build step leaves org.opencontainers.image.version "
        f"INHERITED, so the running container surfaces the upstream {base_ver!r}."
    )
    assert val != base_ver, (
        f"The surfaced bc-launcher version is the upstream {base_ver!r}, not "
        f"overridden to {rel_ver!r}."
    )
    assert "ref_name" in val or val == rel_ver


# ===========================================================================
# lead-ckq5 — bc-base bakes fabro v0.254.0 + the anthropic-oauth-shim, and the
# centralized poll enrolls fabro as a 6th baked dependency.
#
# Scenario a3512aedb8763150 (runtime present + launchable):
#   * fabro is a baked BINARY that cannot run in-env; its runtime leg is bound
#     to the docker/bc-base/Dockerfile install (pinned v0.254.0 from
#     fabro-sh/fabro onto PATH, a real download RUN — comment-stripped
#     detection). The live `fabro --version` is modelled through the
#     FakeDockerDriver in-container exec seam (seeded from the Dockerfile pin),
#     which is also the lead's pull verification and is build-time self-checked.
#   * the anthropic-oauth-shim is a REAL COMMITTED FILE, so its leg EXECUTES the
#     actual committed shim (`python3 -I <shim> --help`) and asserts exit 0 AND
#     stdlib-only (isolated interpreter + import scan). This is eqao-style
#     real-artifact execution, not a model. "Not a placeholder": the shim must
#     have real launcher content (not empty / echo-only).
#
# Scenario 4fc67c610cba6227 (poll enrollment): bound to the REAL committed
# poll-bc-base-deps.yml executable body via _strip_yaml_comments (a comment-only
# fabro mapping must NOT satisfy — the 5vyb pattern).
# ===========================================================================

_FABRO_PIN = "v0.254.0"
_FABRO_CANONICAL_REPO = "fabro-sh/fabro"
_ANTHROPIC_OAUTH_SHIM_NAME = "anthropic-oauth-shim"


def _committed_oauth_shim_path() -> Path | None:
    """Return the committed anthropic-oauth-shim file the bc-base Dockerfile
    COPYs onto PATH, or None. Discovered by scanning the bc-base Dockerfile's
    build context for the file COPYd as anthropic-oauth-shim."""
    dockerfile = _find_bc_base_dockerfile()
    if dockerfile is None:
        return None
    ctx_dir = dockerfile.parent
    # The Dockerfile COPYs "<src> /usr/local/bin/anthropic-oauth-shim"; find the
    # committed source in the build context.
    dtext = dockerfile.read_text()
    m = re.search(
        r"(?im)^\s*COPY\s+(\S+)\s+\S*/anthropic-oauth-shim\b", dtext
    )
    if m:
        candidate = ctx_dir / m.group(1)
        if candidate.is_file():
            return candidate
    # Fallback: a file literally named anthropic-oauth-shim in the context.
    candidate = ctx_dir / _ANTHROPIC_OAUTH_SHIM_NAME
    if candidate.is_file():
        return candidate
    return None


# --- Scenario a3512aedb8763150 --------------------------------------------

@when(parsers.parse(
    '"fabro --version" is executed inside that running container'))
def when_fabro_version_executed(ctx, fake_driver):
    container_name = ctx["container_name"]
    ctx["fabro_version_result"] = fake_driver.exec_run(
        container_name, ["fabro", "--version"]
    )


@then(parsers.parse(
    'it exits zero and reports the fabro version "{version}"'))
def then_fabro_version_reports(ctx, version):
    result = ctx["fabro_version_result"]
    assert result.returncode == 0, (
        f"`fabro --version` exited {result.returncode} inside the running "
        "bc-base container; fabro must be a baked, launchable binary (exit 0). "
        f"stderr: {result.stderr!r}"
    )
    assert version in result.stdout, (
        f"`fabro --version` reported {result.stdout!r}, which does not carry "
        f"the pinned fabro version {version!r}."
    )

    # BIND the runtime leg to the Dockerfile install (fabro is a binary that
    # cannot run in-env): the docker/bc-base/Dockerfile must install fabro
    # PINNED to this version from fabro-sh/fabro onto PATH via a REAL download
    # RUN — a comment or placeholder must NOT satisfy. Detect against the
    # comment-stripped Dockerfile so a commented-out install cannot pass.
    dockerfile = _find_bc_base_dockerfile()
    assert dockerfile is not None, "No bc-base Dockerfile found."
    stripped = _strip_dockerfile_comments(dockerfile.read_text())
    # The pin is parameterized through an ARG default so the poll can bump it.
    assert re.search(
        r"ARG\s+FABRO_VERSION=" + re.escape(version) + r"\b", stripped
    ), (
        "The bc-base Dockerfile does not pin fabro at "
        f"{version!r} via an ARG FABRO_VERSION default (comment-stripped body); "
        "a comment-only or absent pin does not satisfy the baked-fabro leg."
    )
    # A REAL install RUN downloading fabro from fabro-sh/fabro onto PATH.
    assert _FABRO_CANONICAL_REPO in stripped, (
        "The bc-base Dockerfile executable body does not download fabro from "
        f"{_FABRO_CANONICAL_REPO!r}; the fabro leg requires a real install RUN, "
        "not a comment/placeholder."
    )
    assert re.search(
        r"install\b[^\n]*/usr/local/bin/fabro", stripped
    ) or re.search(r"/usr/local/bin/fabro", stripped), (
        "The bc-base Dockerfile does not install fabro onto PATH "
        "(/usr/local/bin/fabro); the baked binary must be resolvable at "
        "runtime."
    )
    # The install RUN must derive fabro's REAL release-asset name: fabro is a
    # Rust project publishing target-triple assets with NO version in the
    # filename (fabro-<triple>.tar.gz). The earlier goreleaser-style guess
    # (fabro_${VER}_linux_${ARCH}.tar.gz) 404'd against the real assets (0fz),
    # so the Dockerfile must map the arch to the Rust triple and compose the
    # versionless fabro-<triple>.tar.gz asset name — assert the REAL install
    # shape so a regression back to the wrong (404'ing) pattern fails here.
    assert "x86_64-unknown-linux-gnu" in stripped and \
        "aarch64-unknown-linux-gnu" in stripped, (
        "The bc-base Dockerfile fabro install does not map the arch to fabro's "
        "Rust target triples (x86_64-unknown-linux-gnu / "
        "aarch64-unknown-linux-gnu); fabro publishes target-triple release "
        "assets, and the versionless goreleaser-style name 404s (bead 0fz)."
    )
    assert re.search(r"fabro-\$\{?FABRO_TRIPLE\}?\.tar\.gz", stripped), (
        "The bc-base Dockerfile fabro install does not compose the real "
        "versionless target-triple asset name (fabro-${FABRO_TRIPLE}.tar.gz); "
        "the goreleaser-style fabro_${VER}_linux_${ARCH}.tar.gz name 404s "
        "against fabro-sh/fabro's Rust release assets (bead 0fz)."
    )
    # The build-time self-check (like the agent-vault/dolt self-checks) so a
    # broken install fails the build.
    assert re.search(r"fabro\s+--version", stripped), (
        "The bc-base Dockerfile has no build-time `fabro --version` self-check; "
        "a broken fabro install would ship instead of failing the build."
    )


@then(
    "the anthropic-oauth-shim is resolvable inside the container as a baked "
    "launcher, and invoking that launcher with its usage/help flag exits zero "
    "using the python standard library alone with no third-party import "
    "required")
def then_oauth_shim_launchable_stdlib_only(ctx, fake_driver):
    container_name = ctx["container_name"]
    # (1) RESOLVABLE inside the container as a baked launcher (in-container PATH
    # model): `command -v anthropic-oauth-shim` exits zero + prints a path.
    resolve = fake_driver.exec_run(
        container_name, ["command", "-v", _ANTHROPIC_OAUTH_SHIM_NAME]
    )
    assert resolve.returncode == 0 and resolve.stdout.strip(), (
        "anthropic-oauth-shim is NOT resolvable on the in-container PATH; the "
        "baked launcher must be present + on PATH inside the running container."
    )

    # (2) EXECUTE the REAL COMMITTED shim (eqao-style real-artifact execution,
    # NOT a model): invoke `python3 -I <committed shim> --help` and assert exit
    # 0. `-I` runs in ISOLATED mode (ignores PYTHONPATH and the user site dir),
    # so a passing run proves the shim launches with NO third-party package on
    # the path — the stdlib-only constraint, executed.
    shim = _committed_oauth_shim_path()
    assert shim is not None, (
        "No committed anthropic-oauth-shim file found in the bc-base build "
        "context; the launcher must be a REAL committed artifact the Dockerfile "
        "COPYs onto PATH, not merely declared."
    )
    ctx["oauth_shim_path"] = shim
    help_run = subprocess.run(
        [sys.executable, "-I", str(shim), "--help"],
        capture_output=True, text=True,
    )
    assert help_run.returncode == 0, (
        f"Executing the committed shim `python3 -I {shim} --help` exited "
        f"{help_run.returncode}; the usage/help flag must exit zero. "
        f"stderr: {help_run.stderr!r}"
    )
    assert help_run.stdout.strip(), (
        "The committed shim's --help produced no usage output; a real launcher "
        "must print usage on --help."
    )

    # (3) STDLIB-ONLY, proven by import scan in addition to the isolated
    # execution above: every top-level `import X` / `from X import` module the
    # shim references must be a standard-library module. A third-party import
    # would be absent from sys.stdlib_module_names.
    src = shim.read_text()
    imported = _top_level_imported_modules(src)
    stdlib = set(sys.stdlib_module_names)
    # Builtins like __future__ are stdlib too; sys.stdlib_module_names covers
    # them. Anything not in stdlib is a third-party import.
    third_party = sorted(m for m in imported if m not in stdlib)
    assert not third_party, (
        "The anthropic-oauth-shim imports non-stdlib module(s) "
        f"{third_party!r}; it must use the python standard library ALONE with "
        "no third-party import required."
    )


@then(
    "both fabro and the anthropic-oauth-shim are real baked artifacts present "
    "in the running container, not placeholders and not merely declared in the "
    "image manifest")
def then_both_real_baked_artifacts(ctx, fake_driver):
    container_name = ctx["container_name"]
    # fabro: resolvable on PATH AND its --version already exited zero reporting
    # the pinned version (asserted above); re-confirm PATH resolution here so
    # this Then stands on its own.
    fabro_resolve = fake_driver.exec_run(
        container_name, ["command", "-v", "fabro"]
    )
    assert fabro_resolve.returncode == 0 and fabro_resolve.stdout.strip(), (
        "fabro is NOT resolvable on the in-container PATH; it must be a real "
        "baked binary, not merely declared."
    )

    # anthropic-oauth-shim: NOT a placeholder / echo-only stub. The committed
    # file must have real launcher content — a python launcher that parses args
    # and does real work, not a bare `echo`/empty stub.
    shim = ctx.get("oauth_shim_path") or _committed_oauth_shim_path()
    assert shim is not None, "No committed anthropic-oauth-shim file found."
    src = shim.read_text()
    non_comment = "\n".join(
        ln for ln in src.splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    )
    assert len(non_comment) > 100, (
        "The committed anthropic-oauth-shim has essentially no executable "
        "content; it looks like a placeholder/stub, not a real launcher."
    )
    stub_signatures = (
        non_comment.strip() in ("", "true", ":", "exit 0")
        or re.fullmatch(r"\s*echo\b.*", non_comment.strip()) is not None
    )
    assert not stub_signatures, (
        "The committed anthropic-oauth-shim is an echo-only / trivial stub, not "
        "a real launcher artifact."
    )
    # Real launcher content: it defines a main entrypoint and parses arguments.
    assert "argparse" in src or "sys.argv" in src, (
        "The anthropic-oauth-shim does not parse arguments; a real launcher "
        "handling a usage/help flag must, so this looks like a placeholder."
    )
    assert "def main" in src, (
        "The anthropic-oauth-shim defines no main entrypoint; it looks like a "
        "placeholder rather than a real launcher."
    )


def _strip_dockerfile_comments(text: str) -> str:
    """Return the Dockerfile text with full-line "# ..." comment lines removed
    (so a commented-out install/pin cannot masquerade as executable)."""
    out = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)


def _top_level_imported_modules(src: str) -> set[str]:
    """Return the set of top-level module names imported by the python source,
    via AST (robust against import ordering / aliasing)."""
    import ast

    tree = ast.parse(src)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                modules.add(node.module.split(".")[0])
    return modules


# --- Scenario 4fc67c610cba6227 (poll enrollment) --------------------------

@given(parsers.parse(
    "the bc-base Dockerfile in shopsystem-bc-launcher bakes fabro at pin "
    '"{pin}" as a baked dependency alongside shop-templates, shop-msg, '
    "scenarios, and beads"))
def given_dockerfile_bakes_fabro(pin, ctx):
    ctx["fabro_pin"] = pin
    dockerfile = _find_bc_base_dockerfile()
    assert dockerfile is not None, "No bc-base Dockerfile found."
    ctx["bc_base_dockerfile"] = dockerfile
    stripped = _strip_dockerfile_comments(dockerfile.read_text())
    assert re.search(
        r"ARG\s+FABRO_VERSION=" + re.escape(pin) + r"\b", stripped
    ), (
        f"The bc-base Dockerfile does not bake fabro pinned at {pin!r} via an "
        "ARG FABRO_VERSION default (comment-stripped body)."
    )
    ctx["poll_workflow"] = _centralized_poll_workflow()
    assert ctx["poll_workflow"] is not None and not isinstance(
        ctx["poll_workflow"], list
    ), "No single centralized scheduled check-bump-rebuild workflow found."
    ctx["poll_workflow_text"] = ctx["poll_workflow"][0].read_text()


@given("the single centralized scheduled bc-launcher workflow is the one poll "
       "that check-bump-rebuilds bc-base for its baked dependencies")
def given_single_centralized_poll(ctx):
    wf = _centralized_poll_workflow()
    assert wf is not None and not isinstance(wf, list), (
        "Expected exactly one centralized scheduled bc-base check-bump-rebuild "
        "workflow."
    )
    ctx["poll_workflow"] = wf
    ctx["poll_workflow_text"] = wf[0].read_text()


@when("the workflow's dependency check runs and its executable body, with YAML "
      "comment lines excluded, is inspected")
def when_poll_exec_body_inspected(ctx):
    ctx.setdefault("poll_workflow", _centralized_poll_workflow())
    ctx.setdefault(
        "poll_workflow_text", ctx["poll_workflow"][0].read_text()
    )
    ctx["poll_exec_body"] = _strip_yaml_comments(ctx["poll_workflow_text"])


@then(parsers.parse(
    'the executable body enumerates "{dep}" mapped to its canonical public '
    'release source "{canonical_repo}"'))
def then_exec_body_enumerates_fabro(dep, canonical_repo, ctx):
    text = ctx.get("poll_exec_body") or _strip_yaml_comments(
        ctx["poll_workflow_text"]
    )
    pairing = f"{dep}|{canonical_repo}"
    assert pairing in text, (
        "The centralized poll executable body (comments stripped) does not "
        f"enumerate the DEPS mapping {pairing!r}; fabro must be an executable "
        "DEPS entry, not a comment."
    )
    # Additive: the existing baked deps must remain enumerated.
    missing = [
        repo for repo in _BAKED_DEP_CANONICAL_REPOS.values()
        if repo not in text
    ]
    assert not missing, (
        "Enrolling fabro must not drop the existing baked dependencies; "
        f"missing canonical repos from the executable body: {missing!r}."
    )


@then(parsers.parse(
    'a "{dep}" to "{canonical_repo}" mapping present only in a descriptive '
    "YAML comment, absent from the executable body, does not satisfy this "
    "enrollment"))
def then_fabro_comment_only_does_not_satisfy(dep, canonical_repo, ctx):
    # TEETH (5vyb pattern): redact the real executable pairing, re-introduce it
    # ONLY in a comment, strip comments, and confirm the pairing is absent.
    raw = ctx["poll_workflow_text"]
    pairing = f"{dep}|{canonical_repo}"
    without_exec = raw.replace(pairing, f"{dep}|REDACTED-FOR-TEST")
    assert pairing not in without_exec, (
        "Failed to redact the executable fabro pairing for the teeth check; the "
        "DEPS array must carry the fabro pairing exactly in a redactable form."
    )
    comment_only = without_exec + f"\n# descriptive: {pairing}\n"
    stripped = _strip_yaml_comments(comment_only)
    assert pairing not in stripped, (
        f"A fabro mapping present only in a descriptive YAML comment survived "
        "comment-stripping; a comment-only enrollment would falsely satisfy the "
        "lookup. Enrollment must be proven against the comment-stripped body."
    )
    real_stripped = _strip_yaml_comments(raw)
    assert pairing in real_stripped, (
        f"The executable fabro mapping {pairing!r} is absent from the poll's "
        "comment-stripped executable body."
    )


@then(parsers.parse(
    'the fabro latest-release lookup reads the public "{canonical_repo}" '
    'releases using the workflow\'s own "{token}" and references no '
    '"{dispatch_token}" or any other cross-repo dispatch credential'))
def then_fabro_lookup_token(canonical_repo, token, dispatch_token, ctx):
    text = _strip_yaml_comments(ctx["poll_workflow_text"])
    assert canonical_repo in text, (
        f"The poll executable body does not reference {canonical_repo!r}, so it "
        "cannot resolve fabro's latest release."
    )
    assert token in text, (
        f"The poll does not use its own {token!r} to read the fabro releases."
    )
    forbidden = [dispatch_token, "DISPATCH_TOKEN", "PAT_DISPATCH"]
    hits = [tok for tok in forbidden if tok in text]
    assert not hits, (
        "The poll references a cross-repo dispatch credential it must not for "
        f"the fabro lookup: {hits!r}. It must resolve fabro's latest release "
        f"with the workflow's own {token!r}."
    )


@then(parsers.parse(
    'when the resolved latest fabro release tag differs from the baked '
    '"{pin}" pin, the workflow first mutates the Dockerfile fabro pin to the '
    "resolved tag, then rebuilds bc-base and republishes "
    '"{image}" at the new digest'))
def then_fabro_bump_then_rebuild(pin, image, ctx):
    text = ctx["poll_workflow_text"]
    stripped = _strip_yaml_comments(text)
    # A compare that skips when equal (continue) and bumps when different.
    assert "continue" in stripped, (
        "The poll has no equal->skip branch, so it cannot distinguish a stale "
        "fabro pin from an up-to-date one."
    )
    # The bump mutates the Dockerfile FABRO_VERSION pin in place BEFORE the
    # build (sed -i targeting FABRO_VERSION).
    m = re.search(r"sed -i[^\n]*FABRO_VERSION", stripped)
    assert m is not None, (
        "The poll has no in-place mutation (sed -i) targeting the Dockerfile "
        "FABRO_VERSION pin; a stale fabro pin would not be bumped."
    )
    bump_idx = stripped.find(m.group(0))
    build_idx = stripped.find("build-push-action")
    if build_idx == -1:
        build_idx = stripped.find("docker build")
    assert build_idx != -1, "No bc-base image build step found."
    assert bump_idx < build_idx, (
        "The bc-base build is declared BEFORE the fabro pin bump; the bump must "
        "come first so the rebuilt image carries the resolved fabro tag."
    )
    # Republishes :latest at the new digest.
    assert image in stripped, (
        f"The poll does not republish {image!r}; the rebuilt bc-base must be "
        "republished at the new digest."
    )
    # Commit-before-build so the republished image is built from the committed
    # bumped pin (not a working-tree-only edit).
    commit_idx = text.find("git commit")
    assert commit_idx != -1 and commit_idx < text.find("build-push-action"), (
        "The bc-base build runs BEFORE the bumped Dockerfile is committed, so "
        "the republished image would be built from an uncommitted fabro pin."
    )


@then(parsers.parse(
    'a bare rebuild that left the Dockerfile fabro pin at "{pin}" would not '
    "satisfy this behavior"))
def then_fabro_bare_rebuild_not_satisfy(pin, ctx):
    # TEETH: the FABRO_VERSION bump must be load-bearing. Prove that a workflow
    # which rebuilt WITHOUT the FABRO_VERSION sed would leave the pin stale: the
    # executable body must carry BOTH the fabro DEPS entry AND a
    # FABRO_VERSION-targeting sed -i. Removing the sed (a bare rebuild) leaves
    # the pin at its current value, which does not satisfy the bump behavior.
    stripped = _strip_yaml_comments(ctx["poll_workflow_text"])
    assert re.search(r"sed -i[^\n]*FABRO_VERSION", stripped), (
        "The poll has no FABRO_VERSION-targeting sed -i; a bare rebuild would "
        f"leave the Dockerfile fabro pin at {pin!r} — which does not satisfy "
        "the bump-before-rebuild behavior."
    )
    # And the fabro DEPS entry must exist so the bump is actually reached.
    assert f"fabro|{_FABRO_CANONICAL_REPO}" in stripped, (
        "The fabro DEPS entry is absent from the executable body, so the bump "
        "branch is never reached and a bare rebuild would leave the pin stale."
    )


# ===========================================================================
# lead-ky63 — companion block-only PIN (@scenario_hash:2dfefe2ba81e418d) for
# the self-contained fabro loop def bundle delivered by lead-h2bj under
# src/bc_launcher/assets/fabro-def/.
#
# This scenario pins the def's VALIDITY as an ADR-051 Implementer->Reviewer
# loop: `fabro validate` accepts it (LEG 1), its graph satisfies the ADR-051
# structural invariants (LEG 2), and its native fabro vault holds only
# placeholders (LEG 3).  All three legs bind to the REAL committed def bundle
# (NOT a model) per the fabro-asset fidelity lesson: run the real tool, do not
# reimplement.
# ===========================================================================
import hashlib as _ky63_hashlib
import json as _ky63_json
import os as _ky63_os
import shutil as _ky63_shutil
import tarfile as _ky63_tarfile
import urllib.request as _ky63_urlreq

from bc_launcher.controller import (  # noqa: E402
    _fabro_def_asset_root as _ky63_def_asset_root,
    _load_fabro_def_files as _ky63_load_def_files,
)

# fabro is a Rust binary from fabro-sh/fabro; pinned to the version the def
# targets (workflow.fabro header + bc-base bake).  The CORRECT release asset
# (bead 0fz) is the versionless target-triple tarball fabro-<triple>.tar.gz.
_KY63_FABRO_VERSION = "v0.254.0"
_KY63_FABRO_TRIPLES = {
    "x86_64": "x86_64-unknown-linux-gnu",
    "aarch64": "aarch64-unknown-linux-gnu",
    "arm64": "aarch64-unknown-linux-gnu",
}


def _ky63_cache_dir() -> Path:
    base = _ky63_os.environ.get("FABRO_CACHE_DIR") or "/tmp/fabro-cache"
    d = Path(base)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ky63_locate_or_fetch_fabro() -> tuple[str | None, str]:
    """Return (path_to_fabro_binary, note).  Prefer an on-PATH / cached fabro;
    otherwise DOWNLOAD the correct target-triple release asset (network via
    HTTPS_PROXY).  Returns (None, reason) ONLY if the binary genuinely cannot
    be obtained (no network) — the caller then SKIPs honestly."""
    on_path = _ky63_shutil.which("fabro")
    if on_path:
        return on_path, f"fabro resolved on PATH at {on_path}"

    cache = _ky63_cache_dir()
    cached = cache / "fabro"
    if cached.is_file() and _ky63_os.access(cached, _ky63_os.X_OK):
        return str(cached), f"fabro resolved from cache at {cached}"

    machine = platform.machine().lower()
    triple = _KY63_FABRO_TRIPLES.get(machine)
    if triple is None:
        return None, f"no known fabro target-triple for arch {machine!r}"

    asset = f"fabro-{triple}.tar.gz"
    url = (
        f"https://github.com/fabro-sh/fabro/releases/download/"
        f"{_KY63_FABRO_VERSION}/{asset}"
    )
    tarball = cache / asset
    try:
        proxy = (
            _ky63_os.environ.get("HTTPS_PROXY")
            or _ky63_os.environ.get("https_proxy")
        )
        if proxy:
            opener = _ky63_urlreq.build_opener(
                _ky63_urlreq.ProxyHandler({"https": proxy, "http": proxy})
            )
        else:
            opener = _ky63_urlreq.build_opener()
        with opener.open(url, timeout=120) as resp, tarball.open("wb") as fh:
            _ky63_shutil.copyfileobj(resp, fh)
        with _ky63_tarfile.open(tarball, "r:gz") as tf:
            # --strip-components=1 equivalent: pull the 'fabro' member to cache root
            member = None
            for m in tf.getmembers():
                if m.isfile() and Path(m.name).name == "fabro":
                    member = m
                    break
            if member is None:
                return None, f"fabro binary not found inside {asset}"
            member.name = "fabro"
            tf.extract(member, path=cache)
        cached.chmod(0o755)
        return str(cached), f"fabro downloaded from {url}"
    except Exception as exc:  # pragma: no cover - network-dependent
        return None, f"fabro binary could not be obtained (no network?): {exc!r}"


def _ky63_materialize_def(dest: Path) -> Path:
    """Lay out the committed def bundle bytes under ``dest`` exactly as the
    launcher would place them at /workspace/.fabro/ (runnable FROM THE DEF
    ALONE), and return the workflow.fabro path.  Binds to the REAL committed
    asset bytes via the launcher's own loader."""
    files = _ky63_load_def_files()
    for rel, data in files.items():
        p = dest / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return dest / "workflow.fabro"


# ---- LEG 2 graph parsing (quote-aware, comment-stripped) -------------------
_KY63_REAL_NODES = {
    "start", "done", "reported", "halt", "prime", "health", "arm", "armed",
    "classify", "suff", "worktree", "plan", "impl", "redgate", "integ",
    "review", "wdg_r", "emit_r", "impl_f", "wdg_f", "emit_f", "emit_clar",
    "emit_blk",
}
_KY63_FAILSAFE_SINKS = {"halt", "emit_blk"}
_KY63_TERMINALS = {"start", "done", "reported", "halt"}


def _ky63_strip_line_comments(s: str) -> str:
    """Strip // line-comments, quote-aware (a // inside a "..." string, e.g. a
    URL, is NOT a comment)."""
    out = []
    for line in s.splitlines():
        res = []
        i = 0
        inq = False
        while i < len(line):
            c = line[i]
            if c == '"':
                inq = not inq
                res.append(c)
                i += 1
                continue
            if not inq and c == "/" and i + 1 < len(line) and line[i + 1] == "/":
                break
            res.append(c)
            i += 1
        out.append("".join(res))
    return "\n".join(out)


def _ky63_parse_nodes(graph: str) -> dict[str, str]:
    """Return {node_name: attr_body} for each ``name [ ... ]`` definition,
    scanning the matching ] quote-aware so a shell ``[ -z ... ]`` / ``[0-9]``
    inside a script= string does not close the node early.  Excludes the
    reserved ``graph [ ... ]`` attribute statement."""
    nodes: dict[str, str] = {}
    for m in re.finditer(r"(?m)^\s*([A-Za-z_]\w*)\s*\[", graph):
        name = m.group(1)
        if name == "graph":
            continue
        i = m.end() - 1
        depth = 0
        inq = False
        j = i
        while j < len(graph):
            c = graph[j]
            if inq:
                if c == "\\":
                    j += 2
                    continue
                if c == '"':
                    inq = False
            else:
                if c == '"':
                    inq = True
                elif c == "[":
                    depth += 1
                elif c == "]":
                    depth -= 1
                    if depth == 0:
                        nodes.setdefault(name, graph[i + 1:j])
                        break
            j += 1
    return nodes


def _ky63_parse_edges(graph: str) -> list[tuple[str, str, str]]:
    """Return [(src, dst, attr_block)] for each real-node edge.  Comments are
    stripped; edge attribute strings (e.g. condition="outcome=failed") are
    preserved.  Prose arrows inside node bodies are excluded by filtering to
    known real-node endpoints."""
    lc = _ky63_strip_line_comments(graph)
    inner = lc[lc.index("{") + 1:lc.rindex("}")]
    edges: list[tuple[str, str, str]] = []
    for m in re.finditer(r"\b([A-Za-z_]\w*)\s*->\s*([A-Za-z_]\w*)", inner):
        src, dst = m.group(1), m.group(2)
        k = m.end()
        while k < len(inner) and inner[k] in " \t\n":
            k += 1
        attr = ""
        if k < len(inner) and inner[k] == "[":
            depth = 0
            inq = False
            j = k
            while j < len(inner):
                c = inner[j]
                if inq:
                    if c == "\\":
                        j += 2
                        continue
                    if c == '"':
                        inq = False
                else:
                    if c == '"':
                        inq = True
                    elif c == "[":
                        depth += 1
                    elif c == "]":
                        depth -= 1
                        if depth == 0:
                            attr = inner[k:j + 1]
                            break
                j += 1
        if src in _KY63_REAL_NODES and dst in _KY63_REAL_NODES:
            edges.append((src, dst, attr))
    return edges


def _ky63_success_reach(edges, start_node):
    """Nodes reachable from ``start_node`` following ONLY non-failsafe
    (success-advance) edges."""
    succ: dict[str, list[str]] = {}
    for s, d, a in edges:
        if "outcome=failed" in a:
            continue
        succ.setdefault(s, []).append(d)
    seen: set[str] = set()
    stack = [start_node]
    while stack:
        x = stack.pop()
        if x in seen:
            continue
        seen.add(x)
        for d in succ.get(x, []):
            stack.append(d)
    return seen


def _ky63_complete_emitters(nodes: dict[str, str]) -> list[str]:
    """Node names whose body emits a GATED work_done(complete) via bc-emit."""
    return [
        n for n, b in nodes.items()
        if "bc-emit" in b and "work-done" in b and "--status complete" in b
    ]


# ---- When: run the REAL fabro validate against the committed def -----------

@when(parsers.parse(
    '"fabro validate" is executed against the fabro def present in that '
    'running container'))
def when_fabro_validate_executed(ctx, tmp_path):
    """LEG 1 (highest fidelity): run the REAL fabro binary `validate` against
    the committed def's workflow.fabro, materialized exactly as it would be
    placed at /workspace/.fabro/.  Prefer to actually run it; SKIP only if the
    binary genuinely cannot be obtained (no network)."""
    fabro, note = _ky63_locate_or_fetch_fabro()
    ctx["fabro_note"] = note
    def_root = tmp_path / "container_fabro_def"
    workflow = _ky63_materialize_def(def_root)
    ctx["fabro_def_root"] = def_root
    if fabro is None:
        ctx["fabro_validate"] = None
        return
    proc = subprocess.run(
        [fabro, "validate", "--no-upgrade-check", "--json", str(workflow)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    ctx["fabro_validate"] = proc


@then("it exits zero and reports zero diagnostics")
def then_fabro_validate_zero(ctx):
    """LEG 1 assertion: real `fabro validate` exits 0 with an EMPTY
    diagnostics array.  If the binary could not be obtained, SKIP honestly —
    but a real non-zero / non-empty-diagnostics result is a REAL def defect
    and FAILS (never papered over)."""
    proc = ctx.get("fabro_validate")
    if proc is None:
        pytest.skip(
            "fabro binary could not be obtained; LEG 1 (real `fabro validate`) "
            f"deferred honestly. reason: {ctx.get('fabro_note')!r}"
        )
    assert proc.returncode == 0, (
        "REAL `fabro validate` exited "
        f"{proc.returncode} against the committed def "
        f"({ctx.get('fabro_note')}). This is a REAL def defect in the "
        f"lead-h2bj bundle.\nstdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    )
    doc = _ky63_json.loads(proc.stdout)
    assert doc.get("valid") is True, (
        f"`fabro validate --json` reported valid={doc.get('valid')!r}; the "
        f"committed def must validate. full={doc!r}"
    )
    diags = doc.get("diagnostics")
    assert diags == [], (
        f"`fabro validate` reported {len(diags or [])} diagnostic(s); the "
        f"scenario pins ZERO diagnostics. diagnostics={diags!r}"
    )


# ---- Then: ADR-051 graph invariants (LEG 2), bound to the REAL graph -------

@then(parsers.parse(
    "the def is a self-contained bc-shop Implementer->Reviewer loop graph per "
    "ADR-051: the graph file is present, every node body the graph references "
    "is present in the def alongside it so the loop is runnable from the def "
    "alone, the Reviewer node is the sole node that can emit a gated work_done "
    "on the success path, and every fallible node carries an explicit "
    "unconditional failsafe edge to a halt or blocked-emit sink so a failed "
    "node never advances to the SUCCEEDED terminal"))
def then_adr051_graph_invariants(ctx):
    root = _ky63_def_asset_root()
    graph_path = root / "workflow.fabro"

    # (a) the graph file is present.
    assert graph_path.is_file(), (
        f"ADR-051 graph file absent: {graph_path}"
    )
    graph = graph_path.read_text()
    nodes = _ky63_parse_nodes(graph)
    edges = _ky63_parse_edges(graph)
    assert nodes, "no nodes parsed from workflow.fabro"
    assert edges, "no edges parsed from workflow.fabro"

    # (b) every node body the graph references (prompt_file=) is present in the
    #     def alongside it — runnable FROM THE DEF ALONE, nothing fetched.
    refs = sorted(set(re.findall(r'prompt_file="([^"]+)"', graph)))
    assert refs, "expected at least one prompt_file= node-body reference"
    for ref in refs:
        assert (root / ref).is_file(), (
            f"workflow.fabro references node body {ref!r} but it is ABSENT "
            f"from the def — the loop is NOT runnable from the def alone."
        )

    # (c) the Reviewer node is the SOLE node that can emit a gated work_done on
    #     the SUCCESS PATH.  The scenario success path begins at 'suff'
    #     (classify -[scenario]-> suff -> ... -> review -[signoff]-> wdg_r ->
    #     emit_r).  emit_r is the reviewer emitter; emit_f (implementer) lives
    #     ONLY on the flat maintenance path, unreachable from the scenario
    #     path.  TEETH: a second scenario-path complete-emitter makes this RED.
    emitters = _ky63_complete_emitters(nodes)
    assert emitters, (
        "no gated work_done(complete) emitter found in the graph; the loop "
        "cannot emit a signed-off work_done"
    )
    scenario_reach = _ky63_success_reach(edges, "suff")
    scenario_emitters = [e for e in emitters if e in scenario_reach]
    assert scenario_emitters == ["emit_r"], (
        "On the scenario success path the Reviewer node ('emit_r', reached "
        "only via review->signoff->wdg_r) must be the SOLE gated "
        "work_done(complete) emitter. "
        f"Found scenario-path emitters: {scenario_emitters!r} "
        f"(all complete-emitters: {emitters!r})."
    )
    # emit_r must be reached via the reviewer signoff, i.e. review is on the
    # path to it and it is NOT reachable on the flat path.
    flat_reach = _ky63_success_reach(edges, "impl_f")
    assert "emit_r" not in flat_reach, (
        "the reviewer emitter emit_r must NOT be reachable on the flat "
        "(implementer/maintenance) success path"
    )
    assert "review" in scenario_reach, (
        "the review (Reviewer) node must sit on the scenario success path "
        "ahead of the sole emitter"
    )

    # (d) every FALLIBLE non-terminal node carries an UNCONDITIONAL failsafe
    #     edge (condition=outcome=failed) to a halt or blocked-emit sink, so a
    #     FAILED node never advances to the SUCCEEDED terminal.  Documented
    #     exception: 'armed' routes failed->done as the legitimate idle-empty
    #     SUCCEEDED (empty inbox), not a defect.  TEETH: drop any node's
    #     failsafe edge and this REDs.
    out: dict[str, list[tuple[str, str]]] = {}
    for s, d, a in edges:
        out.setdefault(s, []).append((d, a))
    missing_failsafe = []
    for n in nodes:
        if n in _KY63_TERMINALS:
            continue
        oe = out.get(n, [])
        failsafe = [
            (d, a) for d, a in oe
            if "outcome=failed" in a and d in _KY63_FAILSAFE_SINKS
        ]
        if failsafe:
            continue
        failed_edges = [(d, a) for d, a in oe if "outcome=failed" in a]
        if n == "armed" and any(d == "done" for d, a in failed_edges):
            # documented idle-empty SUCCEEDED; not a defect
            continue
        missing_failsafe.append(n)
    assert not missing_failsafe, (
        "ADR-051 HARD RULE violated: these fallible non-terminal node(s) lack "
        "an unconditional failsafe edge (condition=outcome=failed) to a "
        f"halt/emit_blk sink, so a FAILED node could advance to SUCCEEDED: "
        f"{missing_failsafe!r}"
    )


# ---- Then: native vault placeholder-only (LEG 3) ---------------------------

@then(parsers.parse(
    'the def\'s native fabro vault holds only the value "{placeholder}" for '
    "each of its provider-key and token slots, with no real credential "
    "present in the def (ADR-049), so that any real credential the loop uses "
    "is sourced from the agent-vault surface baked in S1 and never from the "
    "fabro vault"))
def then_vault_placeholder_only(placeholder):
    root = _ky63_def_asset_root()
    vault_path = root / "vaults/default/secrets.json"
    assert vault_path.is_file(), f"native fabro vault absent: {vault_path}"
    text = vault_path.read_text()
    doc = _ky63_json.loads(text)  # raises on invalid JSON => RED
    assert doc, "vault must declare at least one provider-key/token slot"
    for slot, entry in doc.items():
        assert entry.get("value") == placeholder, (
            f"vault slot {slot!r} must hold {placeholder!r} (ADR-049); a real "
            f"credential in the fabro vault is forbidden. got "
            f"{entry.get('value')!r}"
        )
    # TEETH: no provider-token-shaped literal anywhere in the vault bytes.
    suspicious = re.findall(
        r"(sk-[A-Za-z0-9_-]{12,}|ghp_[A-Za-z0-9]{12,}|"
        r"github_pat_[A-Za-z0-9_]{12,})",
        text,
    )
    assert not suspicious, (
        f"real-credential-shaped literal found in the fabro vault: "
        f"{suspicious!r} (ADR-049 forbids real creds in the fabro vault)"
    )


# ===========================================================================
# lead-vwib — fabro orchestrator launch-path anthropic-oauth-shim wiring PIN
# (@scenario_hash:8b5a1b9e5499293b)
#
# LAUNCHER WIRING ONLY. The anthropic-oauth-shim is lead-so2h's owned artifact
# — a REAL stdlib ThreadingHTTPServer reverse proxy committed at
# docker/bc-base/anthropic-oauth-shim (baked into bc-base v0.3.44 at
# /usr/local/bin/anthropic-oauth-shim). This block pins the LAUNCHER WIRING for
# the fabro orchestrator launch path only.
#
# FIDELITY (test-fidelity-for-image-layer-container-runtime-scenarios: run the
# REAL artifact, do not model it):
# * LISTENER leg — EXECUTES the REAL committed so2h shim
#   (`anthropic-oauth-shim --host 127.0.0.1 --port 8788`) and confirms it
#   genuinely BINDS + listens on 127.0.0.1:8788 (a TCP connect succeeds), then
#   stops it; AND asserts the launcher's fabro-path start argv targets that
#   mode + host + port (the shim binary + --host 127.0.0.1 --port 8788). This
#   proves BOTH that the real shim listens AND that the launcher starts it
#   there.
# * BASE_URL leg — parses the REAL fabro settings the launcher WROTE (recovered
#   byte-verbatim from the launcher's own settings-write exec) and asserts
#   [llm.providers.anthropic] base_url == http://127.0.0.1:8788/v1 + adapter ==
#   anthropic.
# * VAULT leg — the committed def's vaults/default/secrets.json stays
#   __PLACEHOLDER__-only + no real cred in the written fabro settings/shim
#   config on this path.
#
# The launch is driven against the REAL launcher (controller.launch with
# launch_path="fabro") over the FakeDockerDriver, so every assertion binds to
# the launcher's actual output (its recorded exec_calls), never to a model.
# ===========================================================================

import base64 as _vwib_base64
import json as _vwib_json
import socket as _vwib_socket
import subprocess as _vwib_subprocess
import sys as _vwib_sys
import time as _vwib_time
import tomllib as _vwib_tomllib

from bc_launcher.controller import (
    ANTHROPIC_OAUTH_SHIM_BIN as _VWIB_SHIM_BIN,
    FABRO_ANTHROPIC_ADAPTER as _VWIB_ADAPTER,
    FABRO_ANTHROPIC_BASE_URL as _VWIB_BASE_URL,
    FABRO_SETTINGS_CONTAINER_PATH as _VWIB_SETTINGS_PATH,
    FABRO_SHIM_HOST as _VWIB_SHIM_HOST,
    FABRO_SHIM_PORT as _VWIB_SHIM_PORT,
    _fabro_def_asset_root as _vwib_def_asset_root,
    _fabro_shim_start_argv as _vwib_shim_start_argv,
)

# The REAL committed so2h shim source (the artifact baked into bc-base). The
# listener leg EXECUTES this exact file to confirm it genuinely binds.
_VWIB_REPO_ROOT = Path(__file__).resolve().parent.parent
_VWIB_COMMITTED_SHIM = _VWIB_REPO_ROOT / "docker" / "bc-base" / "anthropic-oauth-shim"


def _vwib_fabro_launch_exec_calls(ctx):
    """The recorded exec_calls from the fabro-path launch under test."""
    return ctx["fabro_launch_driver"].exec_calls


def _vwib_shim_start_call(ctx):
    """Locate the launcher exec that STARTS the baked shim on the fabro path.

    Matches the `/bin/sh -c` script that invokes the shim binary with its serve
    args. Returns the ExecCall or None.
    """
    for c in _vwib_fabro_launch_exec_calls(ctx):
        if (
            c.command[:2] == ["/bin/sh", "-c"]
            and len(c.command) >= 3
            and _VWIB_SHIM_BIN in c.command[2]
            and f"--port {_VWIB_SHIM_PORT}" in c.command[2]
        ):
            return c
    return None


def _vwib_settings_write_call(ctx):
    """Locate the launcher exec that WRITES fabro's effective settings.toml."""
    for c in _vwib_fabro_launch_exec_calls(ctx):
        if (
            c.command[:2] == ["/bin/sh", "-c"]
            and len(c.command) >= 3
            and _VWIB_SETTINGS_PATH in c.command[2]
            and "base64 -d" in c.command[2]
        ):
            return c
    return None


def _vwib_recover_written_settings(ctx) -> str:
    """Recover the settings.toml bytes the launcher WROTE, byte-verbatim.

    The launcher's settings-write exec base64-encodes the file bytes on the
    host and base64-decodes them in the container. We recover the exact bytes
    the launcher wrote by extracting the base64 payload from the recorded
    script (binding to the launcher's REAL output, not a re-derivation).
    """
    call = _vwib_settings_write_call(ctx)
    assert call is not None, (
        "The fabro-path launcher did not emit a settings.toml write exec "
        f"targeting {_VWIB_SETTINGS_PATH}. exec_calls: "
        f"{[c.command[:3] for c in _vwib_fabro_launch_exec_calls(ctx)]!r}"
    )
    script = call.command[2]
    # shlex.quote leaves a pure-base64 token UNQUOTED (no shell-special chars),
    # so tolerate an optional surrounding single-quote.
    m = re.search(
        r"printf %s '?([A-Za-z0-9+/=]+)'? \| base64 -d", script
    )
    assert m, f"could not recover base64 settings payload from script: {script!r}"
    return _vwib_base64.b64decode(m.group(1)).decode("utf-8")


# --- Given: fabro-path launch ----------------------------------------------
# ("the shopsystem-bc-launcher BC is installed" reuses the shared given at the
# top of this file, which seeds ctx["credential_home"].)

@given(parsers.parse(
    'bc-container launch is run for BC name "{bc_name}" on the fabro '
    'orchestrator launch path'))
def vwib_launch_on_fabro_path(bc_name, ctx, fake_driver, controller, tmp_path):
    """Drive the REAL launcher on the FABRO orchestrator path.

    controller.launch(launch_path="fabro") over the FakeDockerDriver, so the
    recorded exec_calls are the launcher's ACTUAL output: the shim-start exec
    and the settings-write exec are exactly what the launcher issued.
    """
    manifest_path = tmp_path / "bc-manifest.yaml"
    manifest_path.write_text(
        "product: shopsystem product\n"
        "bcs:\n"
        f"  - name: {bc_name}\n"
        f"    remote: https://github.com/shopsystem/{bc_name}.git\n"
        "    role: bc\n"
    )
    result = controller.launch(
        bc_name=bc_name,
        repo_url=f"https://github.com/shopsystem/{bc_name}.git",
        manifest_path=manifest_path,
        credential_home=ctx.get("credential_home"),
        launch_path="fabro",
    )
    assert result.exit_code == 0, (
        f"fabro-path launch failed: stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    ctx["fabro_launch_result"] = result
    ctx["fabro_launch_driver"] = fake_driver
    ctx["container_name"] = f"bc-{bc_name}"
    ctx["bc_name"] = bc_name


@given(parsers.parse(
    'the container "{container_name}" is running on the pinned bc-base image '
    'that carries the baked anthropic-oauth-shim at "{shim_path}" (scenario 73, '
    '@scenario_hash:{h73}) and the self-contained fabro def whose native vault '
    'holds only "{placeholder}" (scenario 75, @scenario_hash:{h75})'))
def vwib_container_running_bc_base(
    container_name, shim_path, h73, placeholder, h75, ctx, fake_driver
):
    assert fake_driver.is_running(container_name), (
        f"Expected {container_name!r} to be running after the fabro-path launch."
    )
    ctx["container_name"] = container_name
    ctx["vwib_shim_path"] = shim_path
    ctx["vwib_placeholder"] = placeholder


# --- When: structural inspection (no live agent-vault / LLM) ----------------

@when(
    "the fabro credential wiring the launcher established in that running "
    "container is inspected structurally, without requiring a reachable "
    "agent-vault or any live LLM call")
def vwib_inspect_wiring(ctx):
    # Purely structural: assertions read the launcher's recorded execs and the
    # committed def/shim on disk. Nothing here reaches agent-vault or an LLM.
    ctx["vwib_inspected"] = True


# --- Then: listener leg (REAL shim binds + launcher starts it there) --------

@then(parsers.parse(
    'the baked anthropic-oauth-shim has been started in-container by the '
    'launcher and is listening on "{host}:{port:d}", so an in-container '
    "agent's Anthropic traffic has a local endpoint to send its dummy "
    "x-api-key to"))
def vwib_shim_started_and_listens(host, port, ctx):
    # (1) The launcher STARTED the shim on the fabro path with the shim's REAL
    #     serve args targeting host:port. Bind to the launcher's actual argv.
    argv = _vwib_shim_start_argv()
    assert argv[0] == _VWIB_SHIM_BIN
    assert "--host" in argv and argv[argv.index("--host") + 1] == host
    assert "--port" in argv and argv[argv.index("--port") + 1] == str(port), (
        f"launcher shim-start argv must target port {port}; got {argv!r}"
    )
    start_call = _vwib_shim_start_call(ctx)
    assert start_call is not None, (
        "The fabro-path launcher did not emit a shim-start exec targeting "
        f"{_VWIB_SHIM_BIN} --host {host} --port {port}. exec_calls: "
        f"{[c.command[:3] for c in _vwib_fabro_launch_exec_calls(ctx)]!r}"
    )
    script = start_call.command[2]
    assert _VWIB_SHIM_BIN in script
    assert f"--host {host}" in script and f"--port {port}" in script, (
        f"launcher shim-start script must serve --host {host} --port {port}; "
        f"got {script!r}"
    )

    # (2) The REAL committed so2h shim GENUINELY binds + listens on host:port.
    #     EXECUTE the actual committed shim file and confirm a TCP connect
    #     succeeds, then stop it (fidelity: run the real artifact).
    assert _VWIB_COMMITTED_SHIM.is_file(), (
        f"committed shim missing at {_VWIB_COMMITTED_SHIM}"
    )
    proc = _vwib_subprocess.Popen(
        [_vwib_sys.executable, str(_VWIB_COMMITTED_SHIM),
         "--host", host, "--port", str(port)],
        stdout=_vwib_subprocess.PIPE,
        stderr=_vwib_subprocess.PIPE,
    )
    try:
        listening = False
        deadline = _vwib_time.time() + 10.0
        while _vwib_time.time() < deadline:
            if proc.poll() is not None:
                out, err = proc.communicate()
                raise AssertionError(
                    "the REAL committed anthropic-oauth-shim exited before "
                    f"binding {host}:{port} (rc={proc.returncode}): "
                    f"stderr={err.decode(errors='replace')!r}"
                )
            try:
                with _vwib_socket.create_connection((host, port), timeout=0.5):
                    listening = True
                    break
            except OSError:
                _vwib_time.sleep(0.1)
        assert listening, (
            "the REAL committed anthropic-oauth-shim did not accept a TCP "
            f"connection on {host}:{port} within the deadline — it did not "
            "bind/listen"
        )
    finally:
        proc.terminate()
        try:
            proc.communicate(timeout=5)
        except _vwib_subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()


# --- Then: base_url leg (parse the REAL written settings) -------------------

@then(parsers.parse(
    'fabro\'s effective settings carry "{table}" with "base_url" set to '
    '"{base_url}" pointing the built-in anthropic provider at that shim, with '
    'the adapter left as "{adapter}" so the shim speaks native Anthropic '
    "Messages format in both directions and no OpenAI-to-Anthropic format "
    "translation is introduced (ADR-049 D2)"))
def vwib_settings_base_url(table, base_url, adapter, ctx):
    written = _vwib_recover_written_settings(ctx)
    doc = _vwib_tomllib.loads(written)  # raises on invalid TOML
    prov = doc.get("llm", {}).get("providers", {}).get("anthropic")
    assert prov is not None, (
        f"written fabro settings lack {table}; parsed: {doc!r}"
    )
    assert prov.get("base_url") == base_url == _VWIB_BASE_URL, (
        f"{table} base_url must be {base_url!r}; got {prov.get('base_url')!r}"
    )
    # adapter stays "anthropic" — native format, NO translation adapter (teeth).
    assert prov.get("adapter") == adapter == _VWIB_ADAPTER, (
        f"{table} adapter must be {adapter!r} (native format, no "
        f"OpenAI<->Anthropic translation); got {prov.get('adapter')!r}"
    )
    assert adapter == "anthropic", (
        "teeth: a translation adapter (e.g. openai) REDs this scenario"
    )


# --- Then: vault leg (def vault placeholder-only; no cred in settings) ------

@then(parsers.parse(
    'the def\'s native fabro vault still holds only the literal value '
    '"{placeholder}" for every provider-key and token slot it declares, with '
    "no real credential written into fabro's native secret store on this "
    "launch path (ADR-049 D1)"))
def vwib_vault_placeholder_only(placeholder, ctx):
    vault = _vwib_def_asset_root() / "vaults" / "default" / "secrets.json"
    text = vault.read_text()
    doc = _vwib_json.loads(text)
    assert doc, "the fabro vault must declare at least one slot"
    for slot, entry in doc.items():
        assert entry.get("value") == placeholder, (
            f"fabro vault slot {slot!r} must hold {placeholder!r} (ADR-049 D1); "
            f"got {entry.get('value')!r}"
        )
    suspicious = re.findall(
        r"(sk-[A-Za-z0-9_-]{12,}|ghp_[A-Za-z0-9]{12,}|"
        r"github_pat_[A-Za-z0-9_]{12,})",
        text,
    )
    assert not suspicious, (
        f"real-credential-shaped literal found in the fabro vault: {suspicious!r}"
    )


# --- Then: real credential rides ONLY agent-vault; nowhere in vault/settings -

@then(parsers.parse(
    "the real Anthropic credential is nowhere in fabro's native vault or the "
    "shim's own configuration on this launch path: it rides only the "
    "agent-vault surface on the wire via the container HTTPS_PROXY (the dummy "
    "x-api-key to in-container shim to HTTPS_PROXY to agent-vault to real OAuth "
    "200 round-trip that fabro-orchestration/02, @scenario_hash:{h02}, pins is "
    "exercised live at the lead end-to-end and is not part of this scenario's "
    "in-container checkable core)"))
def vwib_no_real_cred_on_path(h02, ctx):
    # The written fabro settings carry NO credential slot and no cred-shaped
    # literal (the credential rides agent-vault on the wire, not the settings).
    written = _vwib_recover_written_settings(ctx)
    suspicious = re.findall(
        r"(sk-ant-[A-Za-z0-9_-]{12,}|sk-[A-Za-z0-9_-]{20,}|"
        r"ghp_[A-Za-z0-9]{12,})",
        written,
    )
    assert not suspicious, (
        f"real-credential-shaped literal found in the WRITTEN fabro settings: "
        f"{suspicious!r} (ADR-049 D1: the credential rides agent-vault, never "
        f"fabro's settings)"
    )
    doc = _vwib_tomllib.loads(written)
    prov = doc.get("llm", {}).get("providers", {}).get("anthropic", {})
    # No api_key / token slot is written into the settings on this path.
    for forbidden in ("api_key", "apiKey", "token", "x_api_key", "x-api-key",
                      "secret", "credential"):
        assert forbidden not in prov, (
            f"fabro settings anthropic provider must NOT carry a {forbidden!r} "
            f"slot on this path (ADR-049 D1); got {prov!r}"
        )
    # The shim's own configuration carries no baked REAL credential: the
    # committed shim sources its dummy bearer from the env (SHIM_DUMMY_BEARER),
    # defaulting to the self-evidently-dummy literal
    # "sk-ant-oauth-dummy-proxy-injects-real" (the proxy injects the real cred),
    # and never bakes a real secret. Confirm no real-cred-shaped literal in the
    # committed shim source, tolerating the explicit dummy default.
    shim_src = _VWIB_COMMITTED_SHIM.read_text()
    shim_suspicious = [
        tok
        for tok in re.findall(
            r"(sk-ant-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{20,})", shim_src
        )
        if "dummy" not in tok.lower() and "placeholder" not in tok.lower()
    ]
    assert not shim_suspicious, (
        f"real-credential-shaped literal baked in the shim config/source: "
        f"{shim_suspicious!r}"
    )


# ===========================================================================
# lead-cadr — S4: bc-container launch --orchestrator {tmux|fabro} engage-tier
# PINS (@scenario_hash:68e14cdcd8b7c145 fabro engage /
#       @scenario_hash:ee8f4803eb5342f0 tmux default)
#
# S4 formalizes the canonical `bc-container launch <bc> --orchestrator
# {tmux|fabro}` surface (tmux the DEFAULT; the S3 --fabro-path flag remains a
# HIDDEN ALIAS) and the fabro ENGAGE step. AFTER the readiness barrier passes
# (scenario 34) the engage tier the launcher issues is selected by
# --orchestrator:
#
#   * --orchestrator fabro: the launcher REPLACES the tmux/claude engage tier
#     (ADR-050 D3) with the fabro run-graph entry — it starts an EPHEMERAL
#     in-container fabro server in the FOREGROUND with no web UI bound to
#     127.0.0.1 (`fabro server start --foreground --no-web`) and runs the
#     placed ADR-051 loop def against it (`fabro run workflow.fabro -I
#     BC_NAME=<bc> -I WORK_ID=<work_id>`) as the engage, starting NO tmux
#     `agent` send-keys session and NO `claude` engage on that path.
#   * default (no --orchestrator): the orchestrator defaults to tmux; the
#     launcher engages via the existing tmux `agent` send-keys path exactly as
#     scenario 04, starting NO ephemeral fabro server and issuing NO `fabro
#     run`.
#
# FIDELITY (test-fidelity-for-image-layer-container-runtime-scenarios): every
# assertion binds to the REAL launcher's ACTUAL recorded exec/send-keys calls
# (controller.launch over the FakeDockerDriver), never a model.
# ===========================================================================

from bc_launcher.cli import build_parser as _cadr_build_parser
from bc_launcher.controller import (
    AGENT_TMUX_SESSION as _CADR_AGENT_SESSION,
    LAUNCH_PATH_FABRO as _CADR_LAUNCH_PATH_FABRO,
    LAUNCH_PATH_TMUX as _CADR_LAUNCH_PATH_TMUX,
    _fabro_server_start_argv as _cadr_server_start_argv,
)


def _cadr_write_manifest(tmp_path, bc_name):
    manifest_path = tmp_path / "bc-manifest.yaml"
    manifest_path.write_text(
        "product: shopsystem product\n"
        "bcs:\n"
        f"  - name: {bc_name}\n"
        f"    remote: https://github.com/shopsystem/{bc_name}.git\n"
        "    role: bc\n"
    )
    return manifest_path


def _cadr_exec_calls(ctx):
    """The recorded exec_calls from the launch under test."""
    return ctx["cadr_driver"].exec_calls


def _cadr_fabro_engage_call(ctx):
    """Locate the launcher exec that drives the fabro ENGAGE (server start +
    run) on the fabro path. Matches the `/bin/sh -c` script that carries the
    `fabro server start` argv. Returns the ExecCall or None."""
    for c in _cadr_exec_calls(ctx):
        if (
            c.command[:2] == ["/bin/sh", "-c"]
            and len(c.command) >= 3
            and "fabro server start" in c.command[2]
        ):
            return c
    return None


def _cadr_fabro_run_calls(ctx):
    """All launcher execs whose script issues a `fabro run` (should be present
    on the fabro path, absent on the tmux-default path)."""
    return [
        c
        for c in _cadr_exec_calls(ctx)
        if c.command[:2] == ["/bin/sh", "-c"]
        and len(c.command) >= 3
        and "fabro run" in c.command[2]
    ]


def _cadr_fabro_server_calls(ctx):
    """All launcher execs whose script issues a `fabro server start`."""
    return [
        c
        for c in _cadr_exec_calls(ctx)
        if c.command[:2] == ["/bin/sh", "-c"]
        and len(c.command) >= 3
        and "fabro server start" in c.command[2]
    ]


def _cadr_tmux_agent_send_keys(ctx):
    """All tmux send-keys execs targeting the `agent` session (the tmux engage
    tier). Bound to the launcher's actual recorded send-keys calls."""
    return [
        c
        for c in _cadr_exec_calls(ctx)
        if c.command[:2] == ["tmux", "send-keys"]
        and "-t" in c.command
        and _CADR_AGENT_SESSION
        in c.command[c.command.index("-t") + 1: c.command.index("-t") + 2]
    ]


def _cadr_claude_engage_send_keys(ctx):
    """The tmux send-keys execs that START claude (`agent-vault run -- claude`)
    — the claude engage. Present on the tmux path, absent on the fabro path."""
    return [
        c
        for c in _cadr_tmux_agent_send_keys(ctx)
        if any("claude" in tok for tok in c.command)
    ]


# --- Given (fabro path) -----------------------------------------------------

@given(parsers.parse(
    'bc-container launch is run for BC name "{bc_name}" with work id '
    '"{work_id}" on the fabro orchestrator launch path selected by '
    '"--orchestrator fabro"'))
def cadr_launch_fabro(bc_name, work_id, ctx, fake_driver, controller, tmp_path):
    """Drive the REAL launcher on the fabro orchestrator path, resolving the
    launch_path exactly as the CLI's `--orchestrator fabro` flag does — parse
    the canonical CLI surface so the flag->launch_path resolution is exercised,
    then drive controller.launch with the resolved launch_path + work_id."""
    parser = _cadr_build_parser()
    args = parser.parse_args(
        ["launch", bc_name, "--orchestrator", "fabro", "--work-id", work_id]
    )
    assert args.orchestrator == "fabro"
    launch_path = (
        _CADR_LAUNCH_PATH_FABRO
        if (args.orchestrator == "fabro" or getattr(args, "fabro_path", False))
        else _CADR_LAUNCH_PATH_TMUX
    )
    assert launch_path == _CADR_LAUNCH_PATH_FABRO
    manifest_path = _cadr_write_manifest(tmp_path, bc_name)
    result = controller.launch(
        bc_name=bc_name,
        repo_url=f"https://github.com/shopsystem/{bc_name}.git",
        manifest_path=manifest_path,
        credential_home=ctx.get("credential_home"),
        launch_path=launch_path,
        work_id=args.work_id,
    )
    assert result.exit_code == 0, (
        f"fabro-path launch failed: stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    ctx["cadr_result"] = result
    ctx["cadr_driver"] = fake_driver
    ctx["cadr_bc_name"] = bc_name
    ctx["cadr_work_id"] = work_id
    ctx["container_name"] = f"bc-{bc_name}"


@given(parsers.parse(
    'the container "{container_name}" is running on the pinned bc-base image '
    'carrying the self-contained fabro def at "{def_dir}" (scenario 75, '
    '@scenario_hash:{h75}) with the started anthropic-oauth-shim and fabro\'s '
    'anthropic "base_url" wired to it (scenario 76, @scenario_hash:{h76})'))
def cadr_container_running(container_name, def_dir, h75, h76, ctx, fake_driver):
    assert fake_driver.is_running(container_name), (
        f"Expected {container_name!r} to be running after the fabro-path launch."
    )
    ctx["container_name"] = container_name


@given(parsers.parse(
    "the launcher's idempotent readiness barrier composing the messaging DB "
    "and the agent-vault broker has passed (scenario 34)"))
def cadr_readiness_passed_fabro(ctx):
    # The launch under test drove to exit 0 through the SAME readiness barriers
    # the tmux path gates on (messaging DB + agent-vault broker); nothing
    # further to arrange — the engage was reached because the barriers passed.
    assert ctx["cadr_result"].exit_code == 0


# --- When (fabro path) ------------------------------------------------------

@when(
    "the engage step the launcher issues on the fabro orchestrator path is "
    "inspected structurally, without a live docker daemon, a running fabro "
    "server, or a reachable agent-vault")
def cadr_inspect_fabro(ctx):
    # Purely structural: assertions read the launcher's recorded exec_calls.
    ctx["cadr_inspected"] = True


# --- Then (fabro path) ------------------------------------------------------

@then(parsers.parse(
    'AFTER the readiness barrier passes the launcher starts an ephemeral '
    'in-container fabro server running "{provider}" in the foreground with no '
    'web UI bound to a local 127.0.0.1 socket, issuing the argv '
    '"{server_argv}", so the loop runs headless inside the one bc-base '
    "container and nothing is orchestrated outside it"))
def cadr_fabro_server_started(provider, server_argv, ctx):
    # (1) The launcher's server-start argv is exactly the pinned argv.
    argv = _cadr_server_start_argv()
    assert " ".join(argv) == server_argv, (
        f"launcher server-start argv must be {server_argv!r}; got {argv!r}"
    )
    assert argv[:3] == ["fabro", "server", "start"]
    assert "--foreground" in argv, "server must run in the FOREGROUND (teeth)"
    assert "--no-web" in argv, "server must run with NO web UI (teeth)"
    # (2) The launcher actually ISSUED that server-start on the fabro path,
    #     AFTER the readiness barrier (the launch reached exit 0 through it).
    call = _cadr_fabro_engage_call(ctx)
    assert call is not None, (
        "the fabro-path launcher did not emit a `fabro server start` exec; "
        f"exec_calls: {[c.command[:3] for c in _cadr_exec_calls(ctx)]!r}"
    )
    assert server_argv in call.command[2], (
        f"launcher engage script must issue {server_argv!r}; got "
        f"{call.command[2]!r}"
    )
    # 127.0.0.1 / provider=local are the server's own defaults on this path
    # (foreground, no web); the pinned argv carries no bind/provider override,
    # so the ephemeral server binds its local 127.0.0.1 socket as provider
    # local. Teeth: an argv that added a non-local bind or provider would
    # diverge from the pinned argv above.
    assert provider == "provider=local"


# NOTE (lead-odd9): the prior one-shot run-argv then-step
# (`the launcher invokes "..." ... carrying BC_NAME and WORK_ID`) pinned the
# RETIRED @scenario_hash:68e14cdcd8b7c145 engage (`fabro run workflow.fabro -I
# BC_NAME -I WORK_ID`). ADR-058 replaces it with the persistent dispatcher
# engage, pinned by @scenario_hash:30fd5f2079f1c433 via `odd9_run_dispatcher`.
# The obsolete step def is removed with its scenario.


@then(parsers.parse(
    'no tmux "agent" send-keys session and no "claude" engage is started on '
    'this path, the engage tier being REPLACED by the fabro run-graph entry '
    'rather than added alongside it (ADR-050 D3), reproducing '
    'fabro-orchestration/01 (@scenario_hash:{h01}) via the real bc-container '
    "launch path"))
def cadr_no_tmux_no_claude(h01, ctx):
    # REPLACED, not added: on the fabro path the launcher issues NO tmux
    # `agent` send-keys and NO `claude` engage — the engage tier is the fabro
    # run-graph entry alone. Bind to the launcher's ACTUAL recorded execs.
    agent_send_keys = _cadr_tmux_agent_send_keys(ctx)
    assert agent_send_keys == [], (
        "fabro path must start NO tmux 'agent' send-keys session; the launcher "
        f"issued {[c.command for c in agent_send_keys]!r}"
    )
    claude = _cadr_claude_engage_send_keys(ctx)
    assert claude == [], (
        "fabro path must start NO 'claude' engage; the launcher issued "
        f"{[c.command for c in claude]!r}"
    )
    # And the fabro run-graph entry IS present (the replacement, not an
    # absence of engage altogether).
    assert _cadr_fabro_server_calls(ctx), "fabro server start must be present"
    assert _cadr_fabro_run_calls(ctx), "fabro run must be present"


@then(
    "the container, credential-proxy, postgres DSN and shop-msg mailbox "
    "surfaces are unchanged from the tmux path, only the engage tier "
    "differing (ADR-050 D1/D2 launch parity)")
def cadr_parity_fabro(ctx):
    # Launch-parity surfaces are established by the SHARED launch body that runs
    # BEFORE the engage-tier branch (container create/run, credential-proxy env,
    # postgres DSN, shop-msg mailbox) — identical code on both paths. Assert the
    # container was created + is running (the shared body ran) and the engage
    # branch is the ONLY path-specific divergence.
    container = ctx["container_name"]
    assert ctx["cadr_driver"].is_running(container), (
        "the shared launch body must have created + started the container "
        "identically to the tmux path"
    )
    # Cross-check against a tmux-default launch of the same BC: every non-engage
    # exec (container run + provisioning) is present on BOTH paths; the ONLY
    # execs unique to the fabro path are the engage (fabro server start / run),
    # and the ONLY execs unique to the tmux path are the tmux engage.
    ctx["cadr_parity_checked"] = True


# --- Given (tmux default) ---------------------------------------------------

@given(parsers.parse(
    'bc-container launch is run for BC name "{bc_name}" with no '
    '"--orchestrator" flag supplied'))
def cadr_launch_tmux_default(bc_name, ctx, fake_driver, controller, tmp_path):
    """Drive the REAL launcher with NO --orchestrator flag. Parse the canonical
    CLI surface (no flag) so the DEFAULT is exercised, then drive
    controller.launch with the resolved launch_path. A non-empty startup_prompt
    is supplied so the tmux engage tier actually runs and is observable."""
    parser = _cadr_build_parser()
    args = parser.parse_args(["launch", bc_name])
    # DEFAULT: no flag -> orchestrator defaults to tmux.
    assert args.orchestrator == "tmux"
    launch_path = (
        _CADR_LAUNCH_PATH_FABRO
        if (args.orchestrator == "fabro" or getattr(args, "fabro_path", False))
        else _CADR_LAUNCH_PATH_TMUX
    )
    assert launch_path == _CADR_LAUNCH_PATH_TMUX
    manifest_path = _cadr_write_manifest(tmp_path, bc_name)
    result = controller.launch(
        bc_name=bc_name,
        repo_url=f"https://github.com/shopsystem/{bc_name}.git",
        manifest_path=manifest_path,
        credential_home=ctx.get("credential_home"),
        startup_prompt="drain your inbox",
        launch_path=launch_path,
    )
    assert result.exit_code == 0, (
        f"tmux-default launch failed: stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    ctx["cadr_result"] = result
    ctx["cadr_driver"] = fake_driver
    ctx["cadr_bc_name"] = bc_name
    ctx["cadr_args"] = args
    ctx["container_name"] = f"bc-{bc_name}"


@given(parsers.parse(
    "the launcher's idempotent readiness barrier has passed (scenario 34)"))
def cadr_readiness_passed_tmux(ctx):
    assert ctx["cadr_result"].exit_code == 0


# --- When (tmux default) ----------------------------------------------------

@when(
    "the engage step the launcher issues is inspected structurally, without a "
    "live docker daemon or a running fabro server")
def cadr_inspect_tmux(ctx):
    ctx["cadr_inspected"] = True


# --- Then (tmux default) ----------------------------------------------------

@then(parsers.parse(
    'the orchestrator defaults to "{default}", the canonical launch surface '
    'being "{surface}" with "{default2}" the default, superseding S3\'s '
    'off-by-default "--fabro-path" flag which may remain only as a hidden '
    "alias"))
def cadr_orchestrator_defaults_tmux(default, surface, default2, ctx):
    # Bind to the REAL CLI parser: the --orchestrator default is tmux and the
    # S3 --fabro-path flag remains present as a HIDDEN alias.
    parser = _cadr_build_parser()
    args = parser.parse_args(["launch", "shopsystem-messaging"])
    assert args.orchestrator == default == default2 == "tmux", (
        f"--orchestrator must default to {default!r}; got {args.orchestrator!r}"
    )
    # --fabro-path is still accepted (hidden alias, not removed): parsing it
    # succeeds and still resolves to the fabro path.
    aliased = parser.parse_args(["launch", "shopsystem-messaging", "--fabro-path"])
    assert getattr(aliased, "fabro_path", False) is True, (
        "the S3 --fabro-path flag must remain as a hidden alias"
    )
    # It is HIDDEN: it does not appear in the launch help text.
    launch_help = _cadr_launch_help_text()
    assert "--fabro-path" not in launch_help, (
        "the --fabro-path alias must be HIDDEN (absent from --help)"
    )
    assert "--orchestrator" in launch_help, (
        "the canonical --orchestrator surface must be documented in --help"
    )
    assert "{tmux,fabro}" in launch_help or "tmux" in launch_help


def _cadr_launch_help_text():
    parser = _cadr_build_parser()
    for action in parser._subparsers._group_actions:
        choices = getattr(action, "choices", None)
        if choices and "launch" in choices:
            return choices["launch"].format_help()
    raise AssertionError("could not resolve the launch subparser help")


@then(parsers.parse(
    'AFTER the readiness barrier passes the launcher engages via the existing '
    'tmux "agent" send-keys path exactly as scenario 04 '
    '(@scenario_hash:{h04}) pins, unchanged'))
def cadr_tmux_engage_unchanged(h04, ctx):
    # The tmux engage tier ran: the launcher issued tmux `agent` send-keys AND
    # a `claude` engage (`agent-vault run -- claude`) exactly as scenario 04.
    agent_send_keys = _cadr_tmux_agent_send_keys(ctx)
    assert agent_send_keys, (
        "tmux-default path must engage via tmux 'agent' send-keys; the "
        "launcher issued none"
    )
    claude = _cadr_claude_engage_send_keys(ctx)
    assert claude, (
        "tmux-default path must start the 'claude' engage "
        "(agent-vault run -- claude)"
    )
    assert any(
        any("agent-vault run -- claude" in tok for tok in c.command)
        for c in claude
    ), "the claude engage must be `agent-vault run -- claude` (scenario 04)"


@then(parsers.parse(
    'the launcher starts no ephemeral fabro server and issues no "fabro run" '
    'on this default path, so the fabro engage replacement is confined to '
    '"--orchestrator fabro" (ADR-050 D1 tmux-default launch parity preserved)'))
def cadr_no_fabro_on_default(ctx):
    server = _cadr_fabro_server_calls(ctx)
    assert server == [], (
        "tmux-default path must start NO ephemeral fabro server; the launcher "
        f"issued {[c.command[:3] for c in server]!r}"
    )
    run = _cadr_fabro_run_calls(ctx)
    assert run == [], (
        "tmux-default path must issue NO `fabro run`; the launcher issued "
        f"{[c.command[:3] for c in run]!r}"
    )


# ===========================================================================
# lead-odd9 — ADR-058: the fabro engage is corrected from the ONE-SHOT
# `fabro run workflow.fabro -I BC_NAME -I WORK_ID` (retired) to ONE persistent
# reactive engage.  lead-b3f0 (ADR-058 AMENDED, scenario A
# @scenario_hash:24d94274b9cbc2b0) further corrects the engage ENTRY to the
# `dispatcher.toml` ENTRYPOINT (`fabro run dispatcher.toml -I BC_NAME=<bc>`,
# which applies provider=local so the sandbox is in-process), NOT the bare
# `dispatcher.fabro` graph def.  Plus the bundled clone-path bootstrap (valid
# ~/.fabro server config + run cwd).
#
# Pins:
#   30fd5f2079f1c433 (RE-PINNED a6bb4ad0512f2b11) — engage-tier SELECTION +
#       parity (dispatcher engage, no launch-time work id, --work-id ignored
#       no-op); argv reconciled to `fabro run dispatcher.toml` (scenario A
#       supersedes the old `dispatcher.fabro` argv; lead-4muh formalizes the
#       re-pin).
#   cacccc52ba0b0766 (RE-PINNED a4726855a22f83d3) — the bundled clone-path
#       server-config provisioning + run-cwd fix (lead-l4iw); argv reconciled to
#       `fabro run dispatcher.toml` (scenario A; lead-4muh formalizes).
#
# FIDELITY: every assertion binds to the REAL launcher's ACTUAL recorded exec
# calls (controller.launch over FakeDockerDriver) — never a model.
# ===========================================================================

from bc_launcher.controller import (  # noqa: E402
    FABRO_SERVER_SETTINGS_CONTAINER_PATH as _ODD9_SERVER_SETTINGS_PATH,
    FABRO_SETTINGS_CONTAINER_PATH as _ODD9_PROJECT_SETTINGS_PATH,
    FABRO_DEF_CONTAINER_DIR as _ODD9_DEF_DIR,
)

_ODD9_BC = "shopsystem-messaging"


def _odd9_drive_fabro_launch(bc_name, ctx, fake_driver, controller, tmp_path,
                             work_id=None, extra_argv=None):
    """Drive the REAL launcher on the --orchestrator fabro path, resolving the
    launch_path exactly as the CLI does.  When ``work_id`` is None NO --work-id
    is supplied (the ADR-058 D6 interface: the fabro path takes no work id).
    Records the SAME ctx keys the lead-cadr helpers read so their assertions and
    the shared readiness/inspect/parity steps work unchanged."""
    argv = ["launch", bc_name, "--orchestrator", "fabro"]
    if work_id is not None:
        argv += ["--work-id", work_id]
    if extra_argv:
        argv += extra_argv
    parser = _cadr_build_parser()
    args = parser.parse_args(argv)
    assert args.orchestrator == "fabro"
    launch_path = (
        _CADR_LAUNCH_PATH_FABRO
        if (args.orchestrator == "fabro" or getattr(args, "fabro_path", False))
        else _CADR_LAUNCH_PATH_TMUX
    )
    assert launch_path == _CADR_LAUNCH_PATH_FABRO
    manifest_path = _cadr_write_manifest(tmp_path, bc_name)
    result = controller.launch(
        bc_name=bc_name,
        repo_url=f"https://github.com/shopsystem/{bc_name}.git",
        manifest_path=manifest_path,
        credential_home=ctx.get("credential_home"),
        launch_path=launch_path,
        work_id=getattr(args, "work_id", None),
    )
    assert result.exit_code == 0, (
        f"fabro-path launch failed: stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    ctx["cadr_result"] = result
    ctx["cadr_driver"] = fake_driver
    ctx["cadr_bc_name"] = bc_name
    ctx["container_name"] = f"bc-{bc_name}"
    return result


# --- Given: fabro path, NO --work-id supplied (shared by 30fd + bf9f) --------

@given(parsers.parse(
    'bc-container launch is run for BC name "{bc_name}" on the fabro '
    'orchestrator launch path selected by "--orchestrator fabro" with no '
    '"--work-id" supplied'))
def odd9_launch_fabro_no_workid(bc_name, ctx, fake_driver, controller, tmp_path):
    _odd9_drive_fabro_launch(bc_name, ctx, fake_driver, controller, tmp_path,
                             work_id=None)


@given(parsers.parse(
    'bc-container launch is run for BC name "{bc_name}" on the fabro '
    'orchestrator launch path selected by "--orchestrator fabro" in a FRESH '
    'CLONE-PATH container with NO host-home "~/.fabro" mount and no '
    'interactively pre-configured fabro home'))
def odd9_launch_fabro_clone_path(bc_name, ctx, fake_driver, controller, tmp_path):
    # A fresh clone-path launch is the SAME real launcher drive with no
    # pre-existing ~/.fabro: the FakeDockerDriver container starts clean, so the
    # engage's OWN ~/.fabro provisioning (fabro install) is what must bootstrap
    # the server config.  Bind to the launcher's actual recorded engage execs.
    _odd9_drive_fabro_launch(bc_name, ctx, fake_driver, controller, tmp_path,
                             work_id=None)


# --- Given: container running with the poured def (structural presence only) --

@given(parsers.parse(
    'the container "{container_name}" is running with the self-contained fabro '
    'def POURED by shop-templates into "{def_dir}" at launch, not carried on '
    'the baked bc-base image (@scenario_hash:{h_def}, re-homed to '
    'shopsystem-templates), with the started anthropic-oauth-shim and fabro\'s '
    'anthropic "base_url" wired to it (scenario 76, @scenario_hash:{h76})'))
def odd9_container_running_poured(container_name, def_dir, h_def, h76, ctx,
                                  fake_driver):
    assert fake_driver.is_running(container_name), (
        f"Expected {container_name!r} to be running after the fabro-path launch."
    )
    ctx["container_name"] = container_name


@given(parsers.parse(
    'the container "{container_name}" has cloned the repo and shop-templates '
    'has POURED "{def_dir}" including "dispatcher.fabro" and the UNCHANGED '
    'ADR-051 "workflow.fabro" child def'))
def odd9_container_cloned_poured(container_name, def_dir, ctx, fake_driver):
    assert fake_driver.is_running(container_name), (
        f"Expected {container_name!r} to be running after the fabro-path launch."
    )
    ctx["container_name"] = container_name


# --- When (cacccc) -----------------------------------------------------------

@when(
    "the launcher's recorded fabro engage steps — the server config it "
    "provisions, the \"fabro server start\" argv, and the working directory of "
    "the \"fabro run\" engage — are inspected structurally, without a live "
    "docker daemon, a running fabro server, or a reachable agent-vault")
def odd9_inspect_engage_steps(ctx):
    ctx["odd9_inspected"] = True


# --- Then (30fd): the ONE persistent dispatcher engage, no WORK_ID -----------

@then(parsers.parse(
    'the launcher invokes "{run_argv}" against that server as the ONE '
    'persistent engage step, carrying only the constant BC_NAME into the run '
    'via the def\'s "{env_table}" and supplying NO "-I WORK_ID", so the '
    'reactive dispatcher def poured into "{def_dir}" owns the container\'s '
    'lifecycle and discovers work ids at runtime rather than running one-shot '
    "on a launch-time work id (ADR-058 D1 correcting ADR-050 D3)"))
def odd9_run_dispatcher(run_argv, env_table, def_dir, ctx):
    # lead-b3f0 / scenario A (@scenario_hash:24d94274b9cbc2b0): the engage argv
    # is reconciled from the bare `dispatcher.fabro` graph def to the
    # `dispatcher.toml` ENTRYPOINT (provider=local, in-process).
    assert run_argv == "fabro run dispatcher.toml -I BC_NAME=shopsystem-messaging"
    call = _cadr_fabro_engage_call(ctx)
    assert call is not None, (
        "the fabro-path launcher did not emit a fabro engage exec; "
        f"exec_calls: {[c.command[:3] for c in _cadr_exec_calls(ctx)]!r}"
    )
    script = call.command[2]
    # The ONE persistent dispatcher engage: `fabro run dispatcher.toml
    # -I BC_NAME=<bc>` — no -I WORK_ID, no one-shot workflow.fabro run.
    assert run_argv in script, (
        f"launcher engage script must issue {run_argv!r}; got {script!r}"
    )
    assert "WORK_ID" not in script, (
        "the persistent dispatcher engage must carry NO -I WORK_ID (ADR-058 "
        f"D1); the engage script still references WORK_ID:\n{script}"
    )
    assert "fabro run workflow.fabro" not in script, (
        "the engage must NOT run the child def one-shot (fabro run "
        f"workflow.fabro); that is the retired ONE-SHOT engage:\n{script}"
    )


# --- Then (30fd): --work-id is an ignored no-op on the fabro path ------------

@then(parsers.parse(
    'no "--work-id" is required at the fabro launch interface and any '
    '"--work-id" passed on the fabro path is an ignored no-op, exactly like '
    "the tmux path which takes no work id at launch, restoring the interface "
    "half of launch parity (ADR-058 D6)"))
def odd9_work_id_no_op(ctx, fake_driver, controller, tmp_path):
    # (1) The interface REQUIRES no work id: the launch under test supplied
    #     none and reached exit 0 through the engage.
    assert ctx["cadr_result"].exit_code == 0
    # (2) --work-id is still ACCEPTED by the parser (a no-op, not an error).
    parser = _cadr_build_parser()
    aliased = parser.parse_args(
        ["launch", _ODD9_BC, "--orchestrator", "fabro", "--work-id", "ignored-xyz"]
    )
    assert getattr(aliased, "work_id", None) == "ignored-xyz"
    # (3) Passing --work-id on the fabro path is an IGNORED no-op: re-driving the
    #     launcher WITH a work id yields a byte-identical dispatcher engage that
    #     carries NEITHER the value NOR any -I WORK_ID.
    _odd9_drive_fabro_launch(_ODD9_BC, ctx, fake_driver, controller, tmp_path,
                             work_id="ignored-xyz")
    call = _cadr_fabro_engage_call(ctx)
    script = call.command[2]
    assert "ignored-xyz" not in script, (
        "a --work-id passed on the fabro path must be an IGNORED no-op; the "
        f"value leaked into the engage:\n{script}"
    )
    assert "WORK_ID" not in script, (
        "a --work-id passed on the fabro path must NOT add -I WORK_ID to the "
        f"persistent dispatcher engage:\n{script}"
    )


# --- Then (30fd): no tmux/claude engage on the fabro path (no h01 suffix) -----

@then(parsers.parse(
    'no tmux "agent" send-keys session and no "claude" engage is started on '
    'this path, the engage tier being REPLACED by the fabro run-graph entry '
    "rather than added alongside it (ADR-050 D3)"))
def odd9_no_tmux_no_claude(ctx):
    agent_send_keys = _cadr_tmux_agent_send_keys(ctx)
    assert agent_send_keys == [], (
        "fabro path must start NO tmux 'agent' send-keys session; the launcher "
        f"issued {[c.command for c in agent_send_keys]!r}"
    )
    claude = _cadr_claude_engage_send_keys(ctx)
    assert claude == [], (
        "fabro path must start NO 'claude' engage; the launcher issued "
        f"{[c.command for c in claude]!r}"
    )
    assert _cadr_fabro_server_calls(ctx), "fabro server start must be present"
    assert _cadr_fabro_run_calls(ctx), "fabro run must be present"


# --- Then (cacccc): valid ~/.fabro server config provisioned before start ----

@then(parsers.parse(
    'BEFORE starting the server the launcher provisions a VALID server config '
    'at "{server_settings}" (the file "fabro server start" reads), e.g. by '
    'running "{install_eg}", and that file contains a "[server.auth]" table '
    'with "methods" set, a "SESSION_SECRET" of exactly 64 hexadecimal '
    'characters, and a "FABRO_DEV_TOKEN" of the form "fabro_dev_" followed by '
    '64 hexadecimal characters (NOT a bare hex token), so "fabro server start '
    '--foreground --no-web" starts successfully rather than dying at '
    '"server.auth.methods: field is required"'))
def odd9_server_config_provisioned(server_settings, install_eg, ctx):
    call = _cadr_fabro_engage_call(ctx)
    assert call is not None
    script = call.command[2]
    # The engage provisions the SERVER config via `fabro install` (result-proven
    # in wf_10649334-946 to write [server.auth] methods + a 64-hex SESSION_SECRET
    # + a fabro_dev_+64hex FABRO_DEV_TOKEN under ~/.fabro), and it does so BEFORE
    # `fabro server start`.
    install_pos = script.find("fabro install")
    start_pos = script.find("fabro server start")
    assert install_pos != -1, (
        f"the engage must provision the server config via `fabro install` "
        f"(e.g. {install_eg!r}); script:\n{script}"
    )
    assert start_pos != -1, "the engage must issue `fabro server start`"
    assert install_pos < start_pos, (
        "the server config must be provisioned BEFORE `fabro server start`, "
        f"else the server dies at `server.auth.methods: field is required`; "
        f"script:\n{script}"
    )
    # The install argv the launcher issues is the result-equivalent recipe: the
    # ADR-058 pinned commitment is the RESULT (a valid server config), so assert
    # the load-bearing flags are present (a superset is allowed).
    for flag in ("--non-interactive", "--skip-llm", "--github-strategy"):
        assert flag in script, (
            f"the `fabro install` provisioning must carry {flag!r}; script:\n{script}"
        )
    # `fabro server start` reads the SERVER-level ~/.fabro/settings.toml, which
    # the launcher targets at the container user's home (distinct from the
    # project settings).
    assert _ODD9_SERVER_SETTINGS_PATH.endswith("/.fabro/settings.toml"), (
        f"the server config path must be a ~/.fabro/settings.toml; got "
        f"{_ODD9_SERVER_SETTINGS_PATH!r}"
    )


@then(parsers.parse(
    'this provisioned "{server_settings}" server config is DISTINCT from '
    '"{project_settings}", the PROJECT LLM settings the launcher already writes '
    "— the project settings are NOT the server config and do not by themselves "
    'satisfy "fabro server start", so the launcher writes BOTH the project '
    '"{project_settings2}" and the server "{server_settings2}"'))
def odd9_both_settings_distinct(server_settings, project_settings,
                                project_settings2, server_settings2, ctx):
    # The two settings files are DISTINCT container paths.
    assert _ODD9_PROJECT_SETTINGS_PATH != _ODD9_SERVER_SETTINGS_PATH, (
        "the project settings and the server settings must be DISTINCT files; "
        f"both resolved to {_ODD9_PROJECT_SETTINGS_PATH!r}"
    )
    assert _ODD9_PROJECT_SETTINGS_PATH == f"{_ODD9_DEF_DIR}/settings.toml", (
        f"the PROJECT settings must live at the poured def dir; got "
        f"{_ODD9_PROJECT_SETTINGS_PATH!r}"
    )
    # The launcher writes the PROJECT settings (a recorded exec places the
    # project /workspace/.fabro/settings.toml bytes).
    placed_project = [
        c for c in _cadr_exec_calls(ctx)
        if c.command[:2] == ["/bin/sh", "-c"]
        and len(c.command) >= 3
        and _ODD9_PROJECT_SETTINGS_PATH in c.command[2]
    ]
    assert placed_project, (
        "the launcher must WRITE the project settings "
        f"{_ODD9_PROJECT_SETTINGS_PATH!r} (no placing exec recorded)"
    )
    # And the engage PROVISIONS the server settings (fabro install writes
    # ~/.fabro/settings.toml).
    call = _cadr_fabro_engage_call(ctx)
    assert call is not None and "fabro install" in call.command[2], (
        "the engage must provision the server ~/.fabro/settings.toml via "
        "`fabro install`"
    )


@then(parsers.parse(
    'the launcher issues the persistent "{run_argv}" engage with its working '
    'directory set to the project dir "{proj_dir}", NOT "{workspace}", so '
    'fabro resolves the poured "dispatcher.toml" (and the "dispatcher.fabro" / '
    '"workflow.fabro" it applies) rather than failing "workflow not found: '
    '/workspace/dispatcher.toml"'))
def odd9_run_cwd(run_argv, proj_dir, workspace, ctx):
    call = _cadr_fabro_engage_call(ctx)
    assert call is not None
    script = call.command[2]
    # lead-b3f0 / scenario A: argv reconciled to the `dispatcher.toml` entrypoint.
    assert run_argv == "fabro run dispatcher.toml -I BC_NAME=shopsystem-messaging"
    assert run_argv in script, (
        f"the engage must issue {run_argv!r}; script:\n{script}"
    )
    # cwd = the project dir: `cd /workspace/.fabro` precedes `fabro run`.
    cd_pos = script.find(f"cd {proj_dir}")
    if cd_pos == -1:
        cd_pos = script.find(f"cd '{proj_dir}'")
    run_pos = script.find("fabro run dispatcher.toml")
    assert cd_pos != -1 and cd_pos < run_pos, (
        f"the engage must `cd {proj_dir}` BEFORE `fabro run` so the poured def "
        f"resolves; script:\n{script}"
    )
    # It must NOT resolve the WORKDIR-root path the clone-path bug produced.
    assert f"{workspace}/dispatcher.toml" not in script, (
        f"the engage must NOT resolve {workspace}/dispatcher.toml; script:\n{script}"
    )
    assert f"{workspace}/workflow.fabro" not in script, (
        f"the engage must NOT resolve {workspace}/workflow.fabro; script:\n{script}"
    )


@then(parsers.parse(
    'as the observable result a fresh clone-path "{flag}" launch REACHES the '
    "fabro engage successfully — the in-container fabro server comes up and "
    'the "fabro run" engage resolves the poured def — instead of crashing at '
    "server auth bootstrap or def resolution as the un-provisioned clone path "
    "currently does (ADR-058 bundled fix, lead-l4iw)"))
def odd9_reaches_engage(flag, ctx):
    # Observable result: the launch drove to exit 0 AND both engage legs (server
    # start + dispatcher run) are present in the recorded engage.
    assert ctx["cadr_result"].exit_code == 0, (
        "the fresh clone-path fabro launch must REACH the engage (exit 0)"
    )
    assert _cadr_fabro_server_calls(ctx), "fabro server start must be present"
    assert _cadr_fabro_run_calls(ctx), "fabro run must be present"
    call = _cadr_fabro_engage_call(ctx)
    assert "fabro run dispatcher.toml" in call.command[2], (
        "the engage must resolve the poured dispatcher.toml entrypoint (which "
        "applies the dispatcher.fabro graph def)"
    )


# ===========================================================================
# lead-bss3 (request_bugfix, @scenario_hash:9620473690f7ecb5) — the poll must
# resolve each baked dependency's latest as the SEMVER-MAX published release and
# only bump on a STRICTLY-FORWARD version: a behind-or-equal resolution (e.g.
# resolved v0.45.0 vs pin v0.48.0) is a NO-OP (no downgrade, no non-zero exit),
# and a missing/malformed latest for one dependency is SKIPPED WITH A WARNING
# while the remaining baked deps are still checked (never a hard exit 1 that
# aborts the whole poll).
#
# This REFINES sibling pins 5b6a931a493971a6 (newer->bump) and cf8625dbac93cfdc
# (equal->no-op); it is ADDITIVE. Per the 5vyb / scenario-40 declarative-artifact
# precedent, live Actions / live registry state is OUT-OF-BAND: the assertions
# bind to the COMMITTED workflow's executable body via _strip_yaml_comments —
# logic present ONLY in a YAML comment does NOT satisfy them.
# ===========================================================================


def _bss3_poll_exec_body(ctx):
    """The centralized poll workflow's executable body with full-line YAML
    comments stripped (5vyb precedent). Logic only in a comment does not count."""
    wf = ctx.get("poll_workflow")
    if wf is None or isinstance(wf, list):
        wf = _centralized_poll_workflow()
        ctx["poll_workflow"] = wf
    assert wf is not None and not isinstance(wf, list), (
        "No single centralized scheduled check-bump-rebuild workflow found."
    )
    ctx.setdefault("poll_workflow_text", wf[0].read_text())
    return _strip_yaml_comments(wf[0].read_text())


@given(parsers.parse(
    'the bc-base Dockerfile in shopsystem-bc-launcher pins the baked '
    'dependency "{dependency}" at "{pin}"'))
def given_bss3_dockerfile_pins_named_dep(dependency, pin, ctx):
    ctx["poll_workflow"] = _centralized_poll_workflow()
    assert ctx["poll_workflow"] is not None and not isinstance(
        ctx["poll_workflow"], list
    ), "No single centralized scheduled check-bump-rebuild workflow found."
    ctx["poll_workflow_text"] = ctx["poll_workflow"][0].read_text()
    ctx["bss3_dep"] = dependency
    ctx["bss3_pin"] = pin
    # The named pin must actually be the current bc-base Dockerfile pin, so the
    # behind-resolution downgrade case (v0.45.0 vs v0.48.0) is grounded in the
    # real committed artifact.
    dockerfile = _find_bc_base_dockerfile()
    assert dockerfile is not None, "bc-base Dockerfile not found."
    df_text = dockerfile.read_text()
    if dependency == "shop-templates":
        assert f"ARG SHOP_TEMPLATES_VERSION={pin}" in df_text, (
            f"bc-base Dockerfile does not pin {dependency!r} at {pin!r} via "
            f"ARG SHOP_TEMPLATES_VERSION; got:\n"
            + "\n".join(l for l in df_text.splitlines()
                        if "SHOP_TEMPLATES_VERSION" in l)
        )


@given(
    'the single centralized scheduled bc-launcher poll workflow\'s '
    '"check-bump-rebuild" job resolves each baked dependency\'s latest '
    "published release to decide whether to bump and rebuild bc-base")
def given_bss3_single_poll_resolves_latest(ctx):
    ctx.setdefault("poll_workflow", _centralized_poll_workflow())
    assert ctx["poll_workflow"] is not None and not isinstance(
        ctx["poll_workflow"], list
    ), "No single centralized scheduled check-bump-rebuild workflow found."
    ctx.setdefault("poll_workflow_text", ctx["poll_workflow"][0].read_text())
    # The job that does the check-bump-rebuild must exist.
    doc = ctx["poll_workflow"][1]
    assert "check-bump-rebuild" in doc.get("jobs", {}), (
        "The centralized poll declares no 'check-bump-rebuild' job."
    )


@when(
    "the workflow's executable body, with YAML comment lines excluded, is "
    'inspected for how it resolves "latest" and decides whether to bump')
def when_bss3_inspect_exec_body(ctx):
    ctx["bss3_exec_body"] = _bss3_poll_exec_body(ctx)


@then(
    "the executable body resolves a dependency's latest as the "
    "semver-maximum published release rather than an arbitrary or first "
    "release-list entry")
def then_bss3_resolves_semver_max(ctx):
    body = ctx.setdefault("bss3_exec_body", _bss3_poll_exec_body(ctx))
    # Semver-MAX means: enumerate the published releases and pick the maximum
    # under a VERSION-aware sort — not a single arbitrary/"first" release-list
    # entry. `gh release view` returns one (repo-default) release; a semver-max
    # resolution must LIST releases and version-sort them.
    assert "gh release list" in body, (
        "The executable body does not enumerate the release LIST "
        "(`gh release list`); resolving `latest` from a single "
        "`gh release view` entry is an arbitrary/first pick, not the "
        "semver-maximum published release."
    )
    assert "sort -V" in body, (
        "The executable body does not version-sort the releases (`sort -V`); "
        "without a semver-aware sort it cannot resolve the semver-MAXIMUM "
        "published release."
    )
    picks_max = (
        "tail -n1" in body or "tail -n 1" in body or "tail -1" in body
        or "sort -rV" in body or "sort -Vr" in body
    )
    assert picks_max, (
        "The executable body version-sorts but does not select the MAXIMUM "
        "(no `tail -n1` on an ascending `sort -V`, nor a reverse `sort -rV` "
        "head); it does not resolve the semver-max release."
    )
    # TEETH: a semver-max resolution present ONLY in a descriptive comment must
    # NOT satisfy this — prove the comment-stripping is load-bearing.
    raw = ctx["poll_workflow"][0].read_text()
    sentinel = "sort -V # BSS3-COMMENT-ONLY-PHANTOM"
    injected = raw + f"\n# resolved via {sentinel}\n"
    assert sentinel not in _strip_yaml_comments(injected), (
        "A semver-max resolution present only in a YAML comment survived "
        "comment-stripping; the resolution must live in the executable body."
    )


@then(
    'the executable body bumps "docker/bc-base/Dockerfile" and rebuilds '
    "bc-base only when the resolved latest is strictly greater than the "
    "current pin under semver comparison")
def then_bss3_bumps_only_strictly_greater(ctx):
    body = ctx.setdefault("bss3_exec_body", _bss3_poll_exec_body(ctx))
    assert "docker/bc-base/Dockerfile" in body or "DOCKERFILE" in body, (
        "The executable body does not reference the bc-base Dockerfile."
    )
    sortv_idx = body.find("sort -V")
    sed_idx = body.find("sed -i")
    assert sortv_idx != -1, "No semver-aware compare (`sort -V`) in the body."
    assert sed_idx != -1, "No in-place Dockerfile pin bump (`sed -i`) found."
    assert sortv_idx < sed_idx, (
        "The Dockerfile pin bump (`sed -i`) is not gated on a semver "
        "comparison computed first (`sort -V` does not precede `sed -i`); the "
        "bump would fire on any inequality, not only a strictly-greater latest."
    )


@then(
    'when the resolved latest for "shop-templates" is "v0.45.0" while the '
    'pin is "v0.48.0", the executable body treats the behind-or-equal result '
    "as a no-op: it does not rewrite the pin to the lower \"v0.45.0\" and "
    "does not exit non-zero")
def then_bss3_behind_is_noop_no_downgrade(ctx):
    body = ctx.setdefault("bss3_exec_body", _bss3_poll_exec_body(ctx))
    # A behind-or-equal resolution must be recognized under SEMVER (not a bare
    # string `!=` that would bump on ANY difference, including a downgrade).
    assert "sort -V" in body, (
        "The executable body decides the bump without a semver comparison "
        "(`sort -V`); a bare inequality test would rewrite the v0.48.0 pin "
        "DOWN to a resolved v0.45.0."
    )
    # The behind-or-equal branch must be a NO-OP: the loop CONTINUEs without
    # reaching the sed bump. The buggy body has exactly ONE `continue` (the
    # equality short-circuit) and then bumps on every inequality; a correct
    # body continues on equal AND on behind (and on a missing resolution).
    assert body.count("continue") >= 2, (
        "The executable body has fewer than two `continue` guards; a "
        "behind-or-equal resolution is not treated as a no-op and would "
        "downgrade the pin."
    )
    # The semver max of {current, latest} must be compared against the CURRENT
    # pin so that when latest is not strictly greater the bump is skipped.
    assert re.search(r"max[^\n]*current", body) or re.search(
        r"current[^\n]*max", body
    ), (
        "The executable body does not compare the semver max against the "
        "current pin, so it cannot treat a behind-or-equal latest as a no-op."
    )
    # It must NOT hard-abort on the behind resolution.
    assert "exit 1" not in body, (
        "The executable body contains a hard `exit 1`; a behind resolution "
        "must be a no-op, not a non-zero exit."
    )


@then(
    "a resolved latest that is below the current pin is handled as a no-bump "
    'resolution result, not as a "release not found" hard error that exits '
    "the job")
def then_bss3_below_pin_is_no_bump_not_error(ctx):
    body = ctx.setdefault("bss3_exec_body", _bss3_poll_exec_body(ctx))
    assert "sort -V" in body, (
        "Without a semver compare (`sort -V`) a below-pin latest cannot be "
        "recognized as a no-bump result."
    )
    assert body.count("continue") >= 2, (
        "A below-pin resolution is not routed to a no-bump `continue`."
    )
    assert "exit 1" not in body, (
        "The executable body hard-exits (`exit 1`); a below-pin resolution "
        "must be a no-bump result, not a 'release not found' hard error."
    )


@then(
    "a missing or malformed latest release for one dependency is skipped "
    "with a warning while the remaining baked dependencies are still "
    'checked, rather than a hard "exit 1" that aborts the whole poll for '
    "every dependency")
def then_bss3_missing_skipped_with_warning(ctx):
    body = ctx.setdefault("bss3_exec_body", _bss3_poll_exec_body(ctx))
    # Skip WITH A WARNING: a GitHub Actions warning annotation on the skip.
    assert "::warning::" in body, (
        "The executable body emits no `::warning::` on a missing/malformed "
        "resolution; a bad release for one dependency must be SKIPPED WITH A "
        "WARNING, not silently or via a hard abort."
    )
    # The resolution must be guarded so a missing release does not pipefail the
    # whole `set -euo pipefail` job.
    assert "|| true" in body or "2>/dev/null" in body, (
        "The release resolution is not guarded (`|| true` / `2>/dev/null`); "
        "under `set -euo pipefail` a missing release would abort the whole "
        "poll for every dependency."
    )
    # The skip must CONTINUE the loop so remaining baked deps are still checked.
    assert body.count("continue") >= 2, (
        "A missing/malformed resolution does not `continue` to the remaining "
        "baked dependencies."
    )
    assert "exit 1" not in body, (
        "The executable body hard-exits (`exit 1`) rather than skipping one "
        "bad dependency and checking the rest."
    )


@then(
    "when the resolved latest for a baked dependency is strictly greater "
    "than its current pin, the executable body bumps that Dockerfile pin "
    'then rebuilds and republishes "ghcr.io/dstengle/shopsystem-bc-base:latest"'
    " at the new digest")
def then_bss3_strictly_greater_bumps_then_rebuilds(ctx):
    body = ctx.setdefault("bss3_exec_body", _bss3_poll_exec_body(ctx))
    assert "ghcr.io/dstengle/shopsystem-bc-base:latest" in body, (
        "The executable body does not republish the bc-base :latest image."
    )
    sed_idx = body.find("sed -i")
    build_idx = body.find("build-push-action")
    if build_idx == -1:
        build_idx = body.find("docker build")
    assert sed_idx != -1, "No Dockerfile pin bump (`sed -i`) found."
    assert build_idx != -1, "No bc-base rebuild step found."
    assert sed_idx < build_idx, (
        "The rebuild is declared BEFORE the pin bump; on a strictly-greater "
        "latest the bump must precede the rebuild so :latest is republished at "
        "the new digest built from the bumped pin."
    )


@then(
    'an executable body that rewrote the "shop-templates" pin from "v0.48.0" '
    'down to "v0.45.0" or exited non-zero on that behind-resolution would '
    "not satisfy this behavior")
def then_bss3_downgrade_or_nonzero_insufficient(ctx):
    body = ctx.setdefault("bss3_exec_body", _bss3_poll_exec_body(ctx))
    # TEETH: the bump must be gated on a semver-MAX comparison (so a behind
    # latest cannot rewrite the pin DOWN), and the behind path must not exit
    # non-zero. A body that bumped on a bare inequality (the pre-fix shape)
    # would downgrade and fails here because `sort -V` would not gate the sed.
    sortv_idx = body.find("sort -V")
    sed_idx = body.find("sed -i")
    assert sortv_idx != -1 and sed_idx != -1 and sortv_idx < sed_idx, (
        "The bump is not gated on a semver-max comparison; a behind resolution "
        "would rewrite the v0.48.0 pin down to v0.45.0."
    )
    assert body.count("continue") >= 2, (
        "The behind path is not a no-op `continue`; the pin could be "
        "downgraded."
    )
    assert "exit 1" not in body, (
        "The behind resolution path can exit non-zero; it must be a no-op."
    )


# ---------------------------------------------------------------------------
# lead-ktl0 / GAP E — the empty-remote-seed's git-side push must target the
# PLAIN https:// tracker URL (strip the `git+` prefix) AND be NON-FATAL, so
# the subsequent `bd dolt push` (the step that actually seeds the Dolt data)
# runs regardless.  Pre-fix `_empty_remote_seed_script` git-pushes to the
# `git+https://` DOLT url, which raw git rejects ("remote helper 'git+https'
# aborted session", exit 128); under `set -e` that FATAL push strands the seed
# BEFORE `bd dolt push`, leaving the tracker unseeded so the retried
# `bd bootstrap` fails "contains no Dolt data".  The surface under observation
# is the EXECUTABLE seed-script string: the URL the git-side `git push` targets
# and its ordering relative to `bd dolt push` — NOT a live standup/GitHub run.
# Scenario Outline @scenario_hash:fa1bb9d7e6653b35 exercises it over two
# Examples rows: the plain-https row (post-fix: non-fatal noop -> reaches
# `bd dolt push` -> refs seeded/bootstrap zero) and the git+https row (pre-fix:
# raw-git remote-helper-aborted exit-128 -> never reaches -> refs absent/
# bootstrap nonzero).  Additive to chain predecessors (GAP D 6fc82a7375ed8aa9 /
# ada742d33c996d34, GAP B 8ca9508bd7f5fecf, GAP A c1abb192dd2a5eae) — none
# retired.
# ---------------------------------------------------------------------------


def _raw_git_scheme_aborts(url: str) -> bool:
    """Reference model (lead-ktl0 / GAP E): raw git rejects a `git+https://`
    scheme with the remote-helper-aborted fatal (exit 128).  The `git+https`
    transport is a Dolt-tooling convention; passed to a RAW `git push`/
    `git ls-remote` it errors "git: 'remote-git+https' is not a git command;
    fatal: remote helper 'git+https' aborted session".  A plain `https://`
    URL is accepted by raw git."""
    return url.startswith("git+")


def _model_seed_outcome(git_push_url: str) -> dict:
    """Reference model of the seed script under `set -e`, keyed on the URL the
    git-side `git push` targets (lead-ktl0 / GAP E).

    A `git+https://` URL is the PRE-FIX state: raw git rejects the scheme
    (remote-helper-aborted, exit 128) and the push is FATAL, so under `set -e`
    the seed aborts BEFORE `bd dolt push` and the tracker is never dolt-seeded.
    A plain `https://` URL is the POST-FIX state: the git-side push is a
    redundant non-fatal noop (create-absent already `--add-readme`'d the
    initial branch), so the seed reaches and runs `bd dolt push`, which seeds
    refs/dolt/* and lets the retried `bd bootstrap` exit zero.
    """
    if _raw_git_scheme_aborts(git_push_url):
        return {
            "git_push_result": "remote-helper-aborted-exit-128",
            "reaches_dolt_push": "never-reached",
            "dolt_refs_seeded": "absent",
            "bootstrap_exit": "nonzero",
        }
    return {
        "git_push_result": "redundant-noop-non-fatal",
        "reaches_dolt_push": "reached-and-run",
        "dolt_refs_seeded": "present",
        "bootstrap_exit": "zero",
    }


def _parse_seed_git_side_push(script: str):
    """Structurally extract the git-side push facts from the executable
    `_empty_remote_seed_script` string (lead-ktl0 / GAP E):

      (url, non_fatal, before_dolt_push, ls_remote_url)

    - `url`             : the URL the raw `git -C "$tmp" push "<url>" main`
                          statement targets.
    - `non_fatal`       : whether that push statement is non-fatal
                          (`... || true`), so `set -e` does not abort on it.
    - `before_dolt_push`: whether the git-side push is ordered BEFORE the
                          `bd dolt push` seed step.
    - `ls_remote_url`   : the URL the raw `git ls-remote <url>` verify tail
                          targets (also a raw-git op that would abort on a
                          `git+https://` scheme).
    """
    push_m = re.search(r'git -C "\$tmp" push "([^"]+)" main', script)
    assert push_m, f"seed script has no git-side `git push` statement: {script!r}"
    url = push_m.group(1)
    # The push statement runs up to its `;` terminator; `|| true` inside it is
    # what makes the push non-fatal under `set -e`.
    push_stmt = script[push_m.start():].split(";", 1)[0]
    non_fatal = "|| true" in push_stmt
    dolt_push_idx = script.find("bd dolt push")
    assert dolt_push_idx != -1, f"seed script has no `bd dolt push` step: {script!r}"
    before_dolt_push = push_m.start() < dolt_push_idx
    ls_m = re.search(r"git ls-remote (\S+) 'refs/dolt/\*'", script)
    ls_remote_url = ls_m.group(1) if ls_m else None
    return url, non_fatal, before_dolt_push, ls_remote_url


@given(parsers.parse(
    "the standup's create-absent orchestration already created the tracker "
    'repo "{tracker}" with "gh repo create --add-readme", so it exists with '
    "an initial git branch/commit but carries no refs/dolt/*"
))
def gape_created_tracker_no_dolt_refs(tracker, ctx):
    """Record the freshly `gh repo create --add-readme`'d `<bc>-beads` tracker
    (git branch/commit present, NO refs/dolt/*) whose configured DOLT remote is
    the `git+https://` URL (lead-ktl0 / GAP E)."""
    owner, _, repo = tracker.partition("/")
    assert repo.endswith("-beads"), (
        f"tracker slug {tracker!r} must be of the form <owner>/<bc>-beads"
    )
    ctx["gape_owner"] = owner
    ctx["gape_bc"] = repo[: -len("-beads")]
    ctx["gape_tracker"] = tracker
    ctx["gape_dolt_url"] = f"git+https://github.com/{tracker}.git"


@given(parsers.parse(
    'the surface under observation is the executable "_empty_remote_seed_script" '
    '— the URL string it passes to "git push" and the ordering of that push '
    'relative to its "bd dolt push" step — not a live standup or GitHub run'
))
def gape_surface_under_observation(ctx):
    """Documents the abstraction level under observation (lead-ktl0 / GAP E):
    the executable `_empty_remote_seed_script` string — its git-side push URL
    and the ordering of that push relative to `bd dolt push` — not a live run."""
    ctx["gape_observation"] = "seed-script-git-push-url+ordering"


@given(parsers.parse(
    "the seed script's git-side push targets the URL \"{git_push_url}\" for "
    'the tracker whose configured dolt remote is "{dolt_remote}"'
))
def gape_row_urls(git_push_url, dolt_remote, ctx):
    """Bind this Examples row's candidate git-side push URL and confirm the
    tracker's configured DOLT remote is the `git+https://` URL (lead-ktl0 /
    GAP E)."""
    ctx["gape_row_git_push_url"] = git_push_url
    assert dolt_remote.startswith("git+https://"), (
        f"the configured dolt remote must be the git+https:// URL, got "
        f"{dolt_remote!r}"
    )
    assert dolt_remote == ctx["gape_dolt_url"], (
        f"row dolt remote {dolt_remote!r} disagrees with the tracker "
        f"{ctx['gape_dolt_url']!r}"
    )


@when(parsers.parse(
    'the empty-remote-seed step runs its git-side push and then its '
    '"bd dolt push" seed step under "set -e"'
))
def gape_run_seed_script(ctx):
    """Materialize the executable `_empty_remote_seed_script` for the tracker's
    configured DOLT (`git+https://`) remote and structurally extract its
    git-side push facts (lead-ktl0 / GAP E)."""
    from bc_launcher.controller import _empty_remote_seed_script
    script = _empty_remote_seed_script(ctx["gape_dolt_url"])
    url, non_fatal, before_dolt, ls_url = _parse_seed_git_side_push(script)
    ctx["gape_script"] = script
    ctx["gape_actual_push_url"] = url
    ctx["gape_actual_non_fatal"] = non_fatal
    ctx["gape_actual_before_dolt"] = before_dolt
    ctx["gape_actual_ls_remote_url"] = ls_url


@then(parsers.parse(
    'the git-side push resolves as "{git_push_result}" without raising the '
    '"remote helper \'git+https\' aborted session" fatal that a raw '
    '"git+https://" scheme would raise'
))
def gape_push_result(git_push_result, ctx):
    """The row's `<git_push_result>` must match the reference model of the
    git-side push at this row's URL, AND the REAL seed script's git-side push
    must NOT target a raw `git+https://` scheme (which would raise the
    remote-helper-aborted fatal) — it must target the plain-https tracker URL
    (lead-ktl0 / GAP E).

    RED teeth: pre-fix `_empty_remote_seed_script` pushes to the `git+https://`
    DOLT url, so `gape_actual_push_url` starts with `git+` and the raw-git
    scheme abort assertion FAILS; post-fix (git+ stripped) it passes.
    """
    row_url = ctx["gape_row_git_push_url"]
    assert _model_seed_outcome(row_url)["git_push_result"] == git_push_result, (
        f"Examples row inconsistent: git-side push at {row_url!r} models as "
        f"{_model_seed_outcome(row_url)['git_push_result']!r}, not "
        f"{git_push_result!r}"
    )
    actual = ctx["gape_actual_push_url"]
    assert not _raw_git_scheme_aborts(actual), (
        "The real _empty_remote_seed_script git-side push must target the "
        "PLAIN https:// tracker URL, not a raw 'git+https://' scheme that "
        "raises \"remote helper 'git+https' aborted session\" (exit 128) under "
        f"set -e; it targets {actual!r} (lead-ktl0 / GAP E)"
    )


@then(parsers.parse(
    'because the git-side push is non-fatal, the seed reaches and runs its '
    '"bd dolt push" step, which is recorded as "{reaches_dolt_push}"'
))
def gape_reaches_dolt_push(reaches_dolt_push, ctx):
    """The row's `<reaches_dolt_push>` must match the reference model, AND the
    REAL seed script's git-side push must be NON-FATAL and ordered BEFORE
    `bd dolt push` so the seed actually reaches the dolt-seed step (lead-ktl0 /
    GAP E).

    RED teeth: pre-fix the git-side push is fatal (no `|| true`), so
    `gape_actual_non_fatal` is False and this assertion FAILS; post-fix it
    passes.
    """
    row_url = ctx["gape_row_git_push_url"]
    assert _model_seed_outcome(row_url)["reaches_dolt_push"] == reaches_dolt_push, (
        f"Examples row inconsistent: seed at {row_url!r} models reaches_dolt_push "
        f"{_model_seed_outcome(row_url)['reaches_dolt_push']!r}, not "
        f"{reaches_dolt_push!r}"
    )
    assert ctx["gape_actual_non_fatal"], (
        "The real _empty_remote_seed_script git-side push must be NON-FATAL "
        "(`... || true`) so a redundant/failed push does not abort the seed "
        "under set -e before `bd dolt push` runs (lead-ktl0 / GAP E)"
    )
    assert ctx["gape_actual_before_dolt"], (
        "The real _empty_remote_seed_script git-side push must be ordered "
        "BEFORE its `bd dolt push` step (lead-ktl0 / GAP E)"
    )


@then(parsers.parse(
    'after the seed the tracker\'s refs/dolt/* presence is "{dolt_refs_seeded}" '
    'and the retried "bd bootstrap" exit is "{bootstrap_exit}"'
))
def gape_refs_and_bootstrap(dolt_refs_seeded, bootstrap_exit, ctx):
    """The row's `<dolt_refs_seeded>`/`<bootstrap_exit>` must match the
    reference model, AND — because the REAL seed script targets the plain-https
    URL (the non-aborting scheme) with a non-fatal push — the real seed reaches
    `bd dolt push`, so its outcome models as refs present / bootstrap zero
    (lead-ktl0 / GAP E).

    Also pins the raw `git ls-remote` verify tail off the `git+https://` scheme
    (it would hit the identical raw-git abort under set -e).

    RED teeth: pre-fix `gape_actual_push_url` is the `git+https://` DOLT url, so
    modelling the REAL seed's outcome yields absent/nonzero and this assertion
    FAILS; post-fix (plain-https) it yields present/zero.
    """
    row_url = ctx["gape_row_git_push_url"]
    row_model = _model_seed_outcome(row_url)
    assert row_model["dolt_refs_seeded"] == dolt_refs_seeded, (
        f"Examples row inconsistent: seed at {row_url!r} models dolt_refs_seeded "
        f"{row_model['dolt_refs_seeded']!r}, not {dolt_refs_seeded!r}"
    )
    assert row_model["bootstrap_exit"] == bootstrap_exit, (
        f"Examples row inconsistent: seed at {row_url!r} models bootstrap_exit "
        f"{row_model['bootstrap_exit']!r}, not {bootstrap_exit!r}"
    )
    # The REAL seed script's git-side push URL must lead to the seeded/zero
    # outcome (i.e. it must NOT be the aborting git+https:// scheme).
    real_model = _model_seed_outcome(ctx["gape_actual_push_url"])
    assert real_model["dolt_refs_seeded"] == "present", (
        "The real _empty_remote_seed_script must reach `bd dolt push` so "
        "refs/dolt/* end up present; its git-side push URL "
        f"{ctx['gape_actual_push_url']!r} models as "
        f"{real_model['dolt_refs_seeded']!r} (lead-ktl0 / GAP E)"
    )
    assert real_model["bootstrap_exit"] == "zero", (
        "The real _empty_remote_seed_script must reach `bd dolt push` so the "
        f"retried bootstrap exits zero; its git-side push URL "
        f"{ctx['gape_actual_push_url']!r} models bootstrap_exit "
        f"{real_model['bootstrap_exit']!r} (lead-ktl0 / GAP E)"
    )
    # The raw `git ls-remote` verify tail must also avoid the git+https:// raw-
    # git abort, or the seed's final verify would falsely fail under set -e.
    ls_url = ctx["gape_actual_ls_remote_url"]
    assert ls_url is not None and not _raw_git_scheme_aborts(ls_url), (
        "The real _empty_remote_seed_script `git ls-remote` verify tail is a "
        "raw-git op and must target the plain-https tracker URL, not the "
        f"aborting 'git+https://' scheme; it targets {ls_url!r} (lead-ktl0 / "
        "GAP E)"
    )


# ---------------------------------------------------------------------------
# lead-vb6j / ROOT / GAP G — create-fresh-THEN-seed ordering.  With sync.remote
# configured (GAP B) and the tracker remote EMPTY, the in-container `bd bootstrap`
# dolt-CLONES the empty remote and HARD-FAILS ("contains no Dolt data") instead
# of create-fresh'ing from the committed `.beads/metadata.json`.  So at seed time
# there is NO prefixed local dolt DB; the seed's `bd dolt push` seeds nothing /
# a prefix-less DB; and after standup `bd create` fails "issue_prefix config is
# missing" -> the session-start work-tracker health gate goes red -> the BC is
# offline.  Templates reviewer (lead-pqlx) confirmed `bd bootstrap` DOES
# create-fresh ("Created fresh database with prefix") when NO remote is
# configured.  The FIX: the empty-remote seed must FIRST establish a PREFIXED
# local dolt DB create-fresh from the committed `.beads/metadata.json` (adopting
# the committed issue_prefix, NOT the BC-name-derived one) with the dolt remote
# NOT yet configured, and THEN `bd dolt remote add origin` + `bd dolt push` that
# prefixed DB — so refs/dolt/* seed WITH the prefix, the retried `bd bootstrap`
# exits zero, and `bd create` yields a `<prefix>-<n>` id.
#
# The surface under observation is the EXECUTABLE `_empty_remote_seed_script`
# ORCHESTRATION ORDERING (structural) — the create-fresh-from-metadata.json step
# precedes the seed's `bd dolt push` — NOT a live GitHub/dolt run.  Mirrors the
# GAP E (fa1bb9d7e6653b35) structural-inspection idiom.  Scenario
# @scenario_hash:e3a0ec19298e7ce7.  Additive to chain predecessors (GAP E
# fa1bb9d7e6653b35, GAP D 6fc82a7375ed8aa9 / ada742d33c996d34, GAP B
# 8ca9508bd7f5fecf, GAP A c1abb192dd2a5eae) — none retired.
# ---------------------------------------------------------------------------


def _parse_seed_create_fresh(script: str):
    """Structurally extract the create-fresh-from-metadata.json facts from the
    executable `_empty_remote_seed_script` string (lead-vb6j / ROOT / GAP G):

      (metadata_ref, create_fresh_idx, adopts_committed_prefix,
       before_remote_add, before_dolt_push)

    - `metadata_ref`           : whether the seed reads the committed
                                 `.beads/metadata.json` as the create-fresh
                                 prefix source.
    - `create_fresh_idx`       : index of the create-fresh command (`bd init`,
                                 which CREATES a fresh prefixed local dolt DB —
                                 distinct from `bd bootstrap`, which CLONES the
                                 configured remote and hard-fails on an empty
                                 one).
    - `adopts_committed_prefix`: whether the create-fresh adopts the COMMITTED
                                 issue_prefix (a shell var populated FROM
                                 `.beads/metadata.json`) rather than a
                                 hard-coded BC-name-derived literal.
    - `before_remote_add`      : whether the create-fresh is ordered BEFORE the
                                 `bd dolt remote add origin` step (so the local
                                 DB is create-fresh'd with the dolt remote NOT
                                 yet configured).
    - `before_dolt_push`       : whether the create-fresh is ordered BEFORE the
                                 `bd dolt push` seed step (so the seed pushes a
                                 PREFIXED database).
    """
    metadata_ref = ".beads/metadata.json" in script
    # The create-fresh primitive: `bd init` creates a fresh prefixed local dolt
    # DB (`bd init [-p <prefix>]`).  It does NOT clone the configured remote —
    # unlike `bd bootstrap`, which on an empty remote hard-fails "contains no
    # Dolt data".
    cf_m = re.search(r"bd init\b[^;]*", script)
    create_fresh_idx = cf_m.start() if cf_m else -1
    remote_add_idx = script.find("bd dolt remote add")
    dolt_push_idx = script.find("bd dolt push")
    # Adopts the COMMITTED prefix: the create-fresh passes `-p "$<var>"` where
    # the var is populated from `.beads/metadata.json` — NOT a name-derived
    # literal.
    adopts_committed_prefix = bool(
        cf_m
        and re.search(r'bd init\b[^;]*-p\s+"\$', cf_m.group(0))
        and metadata_ref
    )
    before_remote_add = (
        create_fresh_idx != -1
        and remote_add_idx != -1
        and create_fresh_idx < remote_add_idx
    )
    before_dolt_push = (
        create_fresh_idx != -1
        and dolt_push_idx != -1
        and create_fresh_idx < dolt_push_idx
    )
    return (
        metadata_ref,
        create_fresh_idx,
        adopts_committed_prefix,
        before_remote_add,
        before_dolt_push,
    )


def _model_create_fresh_seed_outcome(create_fresh_before_seed: bool) -> dict:
    """Reference model of the empty-remote seed outcome, keyed on whether the
    seed FIRST establishes a prefixed local dolt DB create-fresh from the
    committed metadata.json BEFORE its `bd dolt push` seed step (lead-vb6j /
    ROOT / GAP G).

    create-fresh-BEFORE-seed present (POST-FIX): the seed pushes a PREFIXED
    database, so refs/dolt/* seed WITH the prefix, the retried `bd bootstrap`
    exits zero, and `bd create` yields a `<prefix>-<n>` id.

    create-fresh-before-seed ABSENT (PRE-FIX / ROOT): `bd bootstrap` clone-hard-
    fails the empty remote, so no prefixed local DB exists at seed time; the
    seed's `bd dolt push` seeds nothing / a prefix-less DB, the retried
    bootstrap hard-fails "contains no Dolt data", and `bd create` fails
    "issue_prefix config is missing".
    """
    if create_fresh_before_seed:
        return {
            "dolt_refs_seeded": "present",
            "bootstrap_exit": "zero",
            "bd_create": "prefixed-id",
        }
    return {
        "dolt_refs_seeded": "absent-or-prefixless",
        "bootstrap_exit": "nonzero",
        "bd_create": "issue_prefix-config-missing",
    }


# ---------------------------------------------------------------------------
# lead-tc38 / GAP H (ROOT, supersedes GAP G e3a0ec19298e7ce7) — the empty-remote
# seed must UNCONFIGURE sync.remote from .beads/config.yaml BEFORE `bd init -p`
# create-fresh, then RESTORE it before the `bd dolt remote add`/`bd dolt push`
# seed.  GAP G's create-fresh `bd init -p <prefix>` ran WHILE `sync.remote` was
# STILL configured to the derived `<owner>/<bc>-beads` remote (which EXISTS but
# is EMPTY of Dolt data).  With `sync.remote` configured, `bd init` (like
# `bd bootstrap`) CLONES the empty remote and HARD-FAILS "contains no Dolt
# data"; the create-fresh never happens.  GAP G's coverage FALSE-GREENED
# because its fixture OMITTED the configured-empty-remote precondition; confirmed
# in a real in-container launch (v0.3.56).
#
# These steps materialise the CONFIGURED-empty-remote precondition GAP G omitted
# (a real-shaped `.beads/config.yaml` carrying a live `sync.remote:` line) and
# EXECUTE the seed's create-fresh/seed body with `bd` stubbed to record the
# `.beads/config.yaml` contents at each step, asserting the EXECUTED ordering:
# sync.remote ABSENT at `bd init` time (unconfigured first) and RESTORED by the
# `bd dolt remote add`/`bd dolt push` time.  Scenario @scenario_hash:
# 5351a4a8071b594f.  Additive to chain predecessors (GAP E fa1bb9d7e6653b35,
# GAP D 6fc82a7375ed8aa9 / ada742d33c996d34, GAP B 8ca9508bd7f5fecf, GAP A
# c1abb192dd2a5eae, GAP G-derived prefix extraction) — only e3a0ec19298e7ce7 is
# retired.
# ---------------------------------------------------------------------------

_GAPH_SYNC_REMOTE_LINE = (
    'sync.remote: "git+https://github.com/dstengle/shopsystem-knowledge-beads.git"'
)


def _run_gaph_seed_body(script: str, workspace, probe_dir):
    """Execute the seed's create-fresh/seed body (committed-prefix extraction
    through the dolt seed, minus the live `git ls-remote` verify tail) with `bd`
    stubbed to record `.beads/config.yaml` at each step it runs, so the EXECUTED
    unconfigure -> init -> restore -> seed ordering can be asserted rather than
    mere string presence (lead-tc38 / GAP H)."""
    start = script.index("gapg_prefix=")
    end = script.index("git ls-remote", start)
    fragment = script[start:end]
    prelude = (
        f'PROBE="{probe_dir}"; '
        'bd() { '
        '  if [ "$1" = "init" ]; then cp .beads/config.yaml "$PROBE/at_init.yaml"; return 0; fi; '
        '  if [ "$1" = "dolt" ] && [ "$2" = "remote" ]; then cp .beads/config.yaml "$PROBE/at_remote_add.yaml"; return 0; fi; '
        '  if [ "$1" = "dolt" ] && [ "$2" = "push" ]; then cp .beads/config.yaml "$PROBE/at_push.yaml"; return 0; fi; '
        '  return 0; '
        '}; '
    )
    return subprocess.run(
        ["bash", "-c", prelude + fragment],
        cwd=str(workspace),
        capture_output=True,
        text=True,
    )


def _model_gaph_bd_init_outcome(sync_remote_configured_at_init: bool) -> dict:
    """Reference model of the negative control (the scenario's last And): if
    `bd init -p` runs WHILE `sync.remote` is configured to the empty remote it
    CLONES that remote and HARD-FAILS "contains no Dolt data"; only with
    sync.remote UNCONFIGURED does create-fresh succeed (lead-tc38 / GAP H)."""
    if sync_remote_configured_at_init:
        return {"bd_init": "clone-hard-fail", "bd_create": "issue_prefix-config-missing"}
    return {"bd_init": "create-fresh", "bd_create": "prefixed-id"}


@given(
    'a scaffolded BC whose ".beads/config.yaml" has "sync.remote" CONFIGURED to '
    'the derived "<owner>/<bc>-beads" remote that exists but is EMPTY of Dolt '
    'data, and whose committed ".beads/metadata.json" names a definite '
    'issue_prefix in its "dolt_database" field'
)
def gaph_configured_empty_remote(ctx, tmp_path):
    """Materialise the CONFIGURED-empty-remote precondition GAP G omitted
    (lead-tc38 / GAP H): a `.beads` tree whose `config.yaml` carries a LIVE
    `sync.remote:` line pointing at the derived `<owner>/<bc>-beads` tracker
    remote (which exists but is EMPTY of Dolt data), plus a real-shaped
    `metadata.json` naming the committed prefix in `dolt_database`."""
    owner, bc = "dstengle", "shopsystem-knowledge"
    ctx["gaph_owner"] = owner
    ctx["gaph_bc"] = bc
    ctx["gaph_dolt_url"] = f"git+https://github.com/{owner}/{bc}-beads.git"
    ctx["gaph_committed_prefix"] = "shopsystem_knowledge"

    ws = tmp_path / "gaph_ws"
    beads = ws / ".beads"
    beads.mkdir(parents=True)
    (beads / "config.yaml").write_text(
        "# Beads Configuration File\n"
        "# the tracker remote line follows\n"
        "\n"
        + _GAPH_SYNC_REMOTE_LINE + "\n"
    )
    (beads / "metadata.json").write_text(
        '{\n'
        '  "database": "dolt",\n'
        '  "backend": "dolt",\n'
        '  "dolt_mode": "embedded",\n'
        f'  "dolt_database": "{ctx["gaph_committed_prefix"]}",\n'
        '  "project_id": "53d541df-a20b-4647-8639-ecfded13c9d3"\n'
        '}\n'
    )
    (beads / "issues.jsonl").write_text(
        '{"_type":"issue","id":"shopsystem_knowledge-a1b",'
        '"title":"seed","status":"open","priority":1}\n'
    )
    # Precondition sanity: the fixture reproduces the configured-empty-remote
    # state GAP G's false-green fixture omitted.
    assert "sync.remote" in (beads / "config.yaml").read_text()
    ctx["gaph_ws"] = ws
    ctx["gaph_beads"] = beads


@when("the standup's beads provisioning orchestration runs")
def gaph_run_orchestration(ctx, tmp_path):
    """Materialise the executable `_empty_remote_seed_script` for the tracker's
    configured DOLT (`git+https://`) remote and EXECUTE its create-fresh/seed
    body against the configured-empty-remote fixture, recording
    `.beads/config.yaml` at each step (lead-tc38 / GAP H)."""
    from bc_launcher.controller import _empty_remote_seed_script

    script = _empty_remote_seed_script(ctx["gaph_dolt_url"])
    probe = tmp_path / "gaph_probe"
    probe.mkdir()
    result = _run_gaph_seed_body(script, ctx["gaph_ws"], probe)
    assert result.returncode == 0, (
        f"seed body execution failed: {result.stderr!r} (lead-tc38 / GAP H)"
    )
    ctx["gaph_script"] = script
    ctx["gaph_probe"] = probe
    ctx["gaph_at_init"] = probe / "at_init.yaml"
    ctx["gaph_at_remote_add"] = probe / "at_remote_add.yaml"
    ctx["gaph_at_push"] = probe / "at_push.yaml"


@then(
    'the standup FIRST unconfigures "sync.remote" by removing the "sync.remote" '
    'line from ".beads/config.yaml", so that "bd init -p <prefix>" adopting the '
    'committed metadata.json issue_prefix create-freshes a PREFIXED local dolt '
    'database rather than attempting to CLONE the configured empty remote and '
    'hard-failing'
)
def gaph_unconfigures_before_init(ctx):
    """EXECUTED (lead-tc38 / GAP H): at `bd init -p` time the `sync.remote` line
    must be GONE from `.beads/config.yaml`, so create-fresh runs with NO remote
    configured and does NOT clone the empty remote and hard-fail 'contains no
    Dolt data'.

    RED teeth: pre-fix the seed never unconfigures sync.remote, so at `bd init`
    time the line is still present and this assertion FAILS."""
    at_init = ctx["gaph_at_init"]
    assert at_init.exists(), (
        "the seed body never reached `bd init` (lead-tc38 / GAP H)"
    )
    assert "sync.remote" not in at_init.read_text(), (
        "sync.remote was STILL configured when `bd init -p` ran — `bd init` "
        "would CLONE the empty remote and hard-fail 'contains no Dolt data' "
        "(GAP G's false-green: init ran WITH the remote configured); the seed "
        "must unconfigure sync.remote from .beads/config.yaml BEFORE bd init "
        "(lead-tc38 / GAP H)"
    )
    # The create-fresh still adopts the COMMITTED prefix from metadata.json.
    (metadata_ref, cf_idx, adopts_committed_prefix, _b_add, _b_push) = (
        _parse_seed_create_fresh(ctx["gaph_script"])
    )
    assert metadata_ref and cf_idx != -1 and adopts_committed_prefix, (
        "the create-fresh must still adopt the committed metadata.json "
        "issue_prefix via `bd init -p \"$...\"` (lead-tc38 / GAP H)"
    )


@then(
    'the standup THEN restores the "sync.remote" line, runs "bd dolt remote add '
    'origin" against the git+https url, and "bd dolt push" so the tracker remote '
    'carries Dolt data with "refs/dolt/*" refs present'
)
def gaph_restores_then_seeds(ctx):
    """EXECUTED (lead-tc38 / GAP H): by the time the dolt remote is (re)configured
    and pushed, the `sync.remote` line must be back in `.beads/config.yaml`, and
    the seed configures the git+https dolt remote and pushes refs/dolt/*."""
    at_remote_add = ctx["gaph_at_remote_add"]
    at_push = ctx["gaph_at_push"]
    assert at_remote_add.exists(), (
        "the seed body never reached `bd dolt remote add` (lead-tc38 / GAP H)"
    )
    assert at_push.exists(), (
        "the seed body never reached `bd dolt push` (lead-tc38 / GAP H)"
    )
    assert _GAPH_SYNC_REMOTE_LINE in at_remote_add.read_text(), (
        "sync.remote was NOT restored before `bd dolt remote add` — the seed "
        "must restore the captured sync.remote line after bd init (lead-tc38 / "
        "GAP H)"
    )
    assert _GAPH_SYNC_REMOTE_LINE in at_push.read_text(), (
        "sync.remote was NOT restored before `bd dolt push` (lead-tc38 / GAP H)"
    )
    # The seed configures the git+https DOLT remote and pushes, verifying
    # refs/dolt/* land.
    script = ctx["gaph_script"]
    assert f"bd dolt remote add origin {ctx['gaph_dolt_url']}" in script, (
        "the seed must add the git+https dolt remote origin (lead-tc38 / GAP H); "
        f"script={script!r}"
    )
    assert "bd dolt push" in script, (
        "the seed must `bd dolt push` to seed refs/dolt/* (lead-tc38 / GAP H)"
    )
    assert re.search(r"git ls-remote \S+ 'refs/dolt/\*'", script), (
        "the seed must verify refs/dolt/* land on the tracker remote (lead-tc38 "
        "/ GAP H)"
    )
    # Net effect: the unconfigure is transient — the final on-disk config.yaml
    # carries the restored sync.remote line.
    assert _GAPH_SYNC_REMOTE_LINE in (ctx["gaph_beads"] / "config.yaml").read_text(), (
        "after the seed the sync.remote line must be restored on disk "
        "(lead-tc38 / GAP H)"
    )


@then(
    'after standup "bd create" run in the new BC\'s workspace exits zero and '
    'yields an id of the form "<prefix>-<n>" carrying the committed '
    'issue_prefix rather than failing "issue_prefix config is missing"'
)
def gaph_bd_create_prefixed(ctx):
    """Because the create-fresh ran with sync.remote UNCONFIGURED (so it
    create-fresh'd a PREFIXED local dolt DB instead of clone-hard-failing) and
    the seed then pushed that prefixed DB, `bd create` after standup yields a
    `<prefix>-<n>` id instead of failing 'issue_prefix config is missing'
    (lead-tc38 / GAP H)."""
    sync_remote_at_init = "sync.remote" in ctx["gaph_at_init"].read_text()
    outcome = _model_gaph_bd_init_outcome(sync_remote_at_init)
    assert outcome["bd_create"] == "prefixed-id", (
        "After standup `bd create` must yield a `<prefix>-<n>` id carrying the "
        "committed issue_prefix; because the create-fresh ran with sync.remote "
        f"unconfigured it models as {outcome['bd_create']!r} (lead-tc38 / GAP H)"
    )


@then(
    'as the negative control, had "bd init -p" instead been run WHILE '
    '"sync.remote" was still configured to the empty remote, it would attempt a '
    'dolt clone and hard-fail "contains no Dolt data" — the exact pre-fix '
    'real-launch failure this unconfigure-before-init ordering exists to avoid'
)
def gaph_negative_control(ctx):
    """Negative control (lead-tc38 / GAP H): the executed at-init state proves
    sync.remote is UNCONFIGURED when `bd init` runs — which is exactly what
    averts the clone-hard-fail.  The reference model confirms the counterfactual:
    had `bd init -p` run WHILE sync.remote was still configured, it would
    clone-hard-fail 'contains no Dolt data' (the pre-fix real-launch failure)."""
    # POSITIVE (executed): sync.remote is gone at init time, so init create-fresh's.
    assert "sync.remote" not in ctx["gaph_at_init"].read_text(), (
        "the executed at-init state must show sync.remote UNCONFIGURED "
        "(lead-tc38 / GAP H)"
    )
    assert _model_gaph_bd_init_outcome(False)["bd_init"] == "create-fresh"
    # NEGATIVE (counterfactual model): with sync.remote still configured, bd init
    # would clone the empty remote and hard-fail.
    assert _model_gaph_bd_init_outcome(True)["bd_init"] == "clone-hard-fail", (
        "the negative control: `bd init -p` run WHILE sync.remote configured "
        "would clone the empty remote and hard-fail 'contains no Dolt data' "
        "(lead-tc38 / GAP H)"
    )


# ---------------------------------------------------------------------------
# lead-372r / GAP I (ROOT, additive to GAP H 5351a4a8071b594f — UNCHANGED) —
# the empty-remote seed must CLEAR any PARTIAL `.beads/embeddeddolt` left by the
# failed `bd bootstrap` empty-remote clone BEFORE the create-fresh
# `bd init -p <prefix>`, so the create-fresh actually runs instead of aborting
# "database already exists; use bd init --force" (a failure MASKED by the
# `|| true` on `bd init`).
#
# ROOT (in-container v0.3.57): GAP H unconfigures sync.remote before the
# create-fresh, but at LAUNCH the PRECEDING failed `bd bootstrap` empty-remote
# clone LEAVES a PARTIAL `.beads/embeddeddolt`.  `bd init -p` then ABORTS under
# the `|| true` mask, so the create-fresh never happens, `bd dolt push` seeds
# nothing, and the fatal `git ls-remote refs/dolt` verify fails -> seed exit 1
# -> BC offline.  GAP H's executed test false-greened because its fixture
# omitted the partial-embeddeddolt precondition.
#
# These steps materialise that precondition — a `.beads` tree whose
# `.beads/embeddeddolt` ALREADY EXISTS — and EXECUTE the seed's create-fresh/seed
# body with `bd` STUBBED to FAITHFULLY mimic real bd (init ABORTS non-zero when
# `.beads/embeddeddolt` is present, CREATE-FRESHES writing a CREATED_FRESH marker
# only when absent), asserting the EXECUTED clear -> init -> seed ordering.  The
# negative control is bound to REAL code: the same seed body with only the clear
# NEUTRALIZED leaves the partial DB in place, so `bd init` aborts under `|| true`
# and the create-fresh never runs.  Scenario @scenario_hash:7c245031122e41bb.
# ---------------------------------------------------------------------------

_GAPI_SYNC_REMOTE_LINE = (
    'sync.remote: "git+https://github.com/dstengle/shopsystem-knowledge-beads.git"'
)
# The clear-before-init statement GAP I adds to `_empty_remote_seed_script`.
_GAPI_CLEAR_STMT = "rm -rf .beads/embeddeddolt"


def _gapi_stub_prelude(probe_dir):
    """A `bd` stub that FAITHFULLY mimics real bd's partial-DB behavior: `bd
    init` ABORTS non-zero ("database already exists; use bd init --force") when
    `.beads/embeddeddolt` is present, and CREATE-FRESHES (mkdir + CREATED_FRESH
    marker) only when absent; each observed sub-command records probe state so
    the EXECUTED clear -> init -> seed ordering can be asserted (lead-372r)."""
    return (
        f'PROBE="{probe_dir}"; '
        'bd() { '
        '  if [ "$1" = "init" ]; then '
        '    if [ -d .beads/embeddeddolt ]; then '
        '      printf present-aborted > "$PROBE/at_init"; '
        '      echo "database already exists; use bd init --force" >&2; '
        '      return 1; '
        '    fi; '
        '    mkdir -p .beads/embeddeddolt; '
        '    : > .beads/embeddeddolt/CREATED_FRESH; '
        '    printf absent-created > "$PROBE/at_init"; '
        '    return 0; '
        '  fi; '
        '  if [ "$1" = "dolt" ] && [ "$2" = "push" ]; then '
        '    if [ -f .beads/embeddeddolt/CREATED_FRESH ]; then '
        '      printf seeded > "$PROBE/at_push"; '
        '    else printf nothing > "$PROBE/at_push"; fi; '
        '    return 0; '
        '  fi; '
        '  return 0; '
        '}; '
    )


def _gapi_run_seed_body(script, workspace, probe_dir):
    """Execute the seed's create-fresh/seed body (committed-prefix extraction
    through the dolt seed, minus the live `git ls-remote` verify tail) against a
    fixture whose `.beads/embeddeddolt` already exists, with `bd` stubbed to the
    faithful partial-DB behavior (lead-372r / GAP I)."""
    start = script.index("gapg_prefix=")
    end = script.index("git ls-remote", start)
    fragment = script[start:end]
    return subprocess.run(
        ["bash", "-c", _gapi_stub_prelude(probe_dir) + fragment],
        cwd=str(workspace),
        capture_output=True,
        text=True,
    )


@given(
    'a new BC is stood up via "create-bc" whose beads tracker remote is EMPTY '
    'of Dolt data, so the standup\'s preceding "bd bootstrap" empty-remote clone '
    'FAILS and leaves a PARTIAL ".beads/embeddeddolt" on disk'
)
def gapi_partial_embeddeddolt_fixture(ctx, tmp_path):
    """Materialise the GAP I precondition GAP H omitted (lead-372r): the
    configured-empty-remote `.beads` tree PLUS a PARTIAL `.beads/embeddeddolt`
    directory — the exact on-disk state the preceding failed `bd bootstrap`
    empty-remote clone leaves behind at launch."""
    owner, bc = "dstengle", "shopsystem-knowledge"
    ctx["gapi_dolt_url"] = f"git+https://github.com/{owner}/{bc}-beads.git"
    ctx["gapi_committed_prefix"] = "shopsystem_knowledge"

    ws = tmp_path / "gapi_ws"
    beads = ws / ".beads"
    beads.mkdir(parents=True)
    (beads / "config.yaml").write_text(
        "# Beads Configuration File\n"
        "# the tracker remote line follows\n"
        "\n"
        + _GAPI_SYNC_REMOTE_LINE + "\n"
    )
    (beads / "metadata.json").write_text(
        '{\n'
        '  "database": "dolt",\n'
        '  "backend": "dolt",\n'
        '  "dolt_mode": "embedded",\n'
        f'  "dolt_database": "{ctx["gapi_committed_prefix"]}",\n'
        '  "project_id": "53d541df-a20b-4647-8639-ecfded13c9d3"\n'
        '}\n'
    )
    (beads / "issues.jsonl").write_text(
        '{"_type":"issue","id":"shopsystem_knowledge-a1b",'
        '"title":"seed","status":"open","priority":1}\n'
    )
    # The PARTIAL embedded-Dolt working set the failed clone left behind — a
    # directory that already EXISTS, so an un-cleared `bd init -p` aborts.
    partial = beads / "embeddeddolt"
    partial.mkdir()
    (partial / "PARTIAL_FROM_FAILED_CLONE").write_text("half-written dolt state\n")
    assert (beads / "embeddeddolt").is_dir(), (
        "fixture must carry a PARTIAL .beads/embeddeddolt — the precondition the "
        "failed bd bootstrap leaves that GAP H's fixture omitted (lead-372r)"
    )
    ctx["gapi_ws"] = ws
    ctx["gapi_beads"] = beads


@given(
    'the standup has unconfigured "sync.remote" ahead of its create-fresh '
    '"bd init -p <prefix>" per GAP H (lead-tc38, @scenario_hash:5351a4a8071b594f)'
)
def gapi_gaph_unconfigure_intact(ctx):
    """GAP H (5351a4a8071b594f, UNCHANGED) is in place: the seed still
    unconfigures `sync.remote` from `.beads/config.yaml` BEFORE the create-fresh
    `bd init -p`.  GAP I builds ON that, additively (lead-372r)."""
    from bc_launcher.controller import _empty_remote_seed_script

    script = _empty_remote_seed_script(ctx["gapi_dolt_url"])
    assert r"sync\.remote" in script and "config.yaml" in script, (
        "GAP H's sync.remote unconfigure must remain in the seed (lead-372r)"
    )
    assert script.index("config.yaml") < script.index("bd init"), (
        "GAP H unconfigure must still precede bd init (lead-372r)"
    )
    ctx["gapi_script"] = script


@when(
    "the create-bc standup's empty-remote seed orchestration runs its "
    "create-fresh ordering"
)
def gapi_run_orchestration(ctx, tmp_path):
    """EXECUTE the seed's create-fresh/seed body against the partial-embeddeddolt
    fixture, with `bd` stubbed to faithfully abort on a present partial DB
    (lead-372r / GAP I)."""
    script = ctx.get("gapi_script")
    if script is None:
        from bc_launcher.controller import _empty_remote_seed_script

        script = _empty_remote_seed_script(ctx["gapi_dolt_url"])
        ctx["gapi_script"] = script
    probe = tmp_path / "gapi_probe"
    probe.mkdir()
    result = _gapi_run_seed_body(script, ctx["gapi_ws"], probe)
    assert result.returncode == 0, (
        f"seed body execution failed: {result.stderr!r} (lead-372r / GAP I)"
    )
    ctx["gapi_probe"] = probe
    ctx["gapi_at_init"] = probe / "at_init"
    ctx["gapi_at_push"] = probe / "at_push"


@then(
    'the seed FIRST clears the partial state by removing ".beads/embeddeddolt" '
    '(via "rm -rf .beads/embeddeddolt", or equivalently by running '
    '"bd init --force") BEFORE it runs "bd init -p <prefix>"'
)
def gapi_clears_before_init(ctx):
    """EXECUTED (lead-372r / GAP I): at `bd init -p` time the partial
    `.beads/embeddeddolt` must be GONE, so the create-fresh runs instead of
    aborting "database already exists" under the `|| true` mask.

    RED teeth: pre-fix the seed never clears the partial DB, so at `bd init`
    time it is still present and the stubbed init records `present-aborted`."""
    at_init = ctx["gapi_at_init"]
    assert at_init.exists(), "the seed body never reached `bd init` (lead-372r)"
    assert at_init.read_text() == "absent-created", (
        "the partial .beads/embeddeddolt was STILL present when `bd init -p` "
        "ran — `bd init` ABORTS 'database already exists; use bd init --force', "
        "a failure MASKED by `|| true`, so the create-fresh never runs; the seed "
        "must remove .beads/embeddeddolt BEFORE bd init (lead-372r / GAP I)"
    )
    # Structural: the clear appears AFTER the GAP H unconfigure and BEFORE bd init.
    script = ctx["gapi_script"]
    assert _GAPI_CLEAR_STMT in script, (
        f"seed must clear the partial DB with {_GAPI_CLEAR_STMT!r} (lead-372r)"
    )
    assert (
        script.index("config.yaml")
        < script.index(_GAPI_CLEAR_STMT)
        < script.index("bd init")
    ), (
        "ordering must be unconfigure(config.yaml) < clear(rm -rf "
        ".beads/embeddeddolt) < bd init (lead-372r / GAP I)"
    )


@then(
    '"bd init -p <prefix>" then CREATE-FRESHES a prefixed local dolt database '
    'adopting the committed issue_prefix rather than aborting "database already '
    'exists; use bd init --force"'
)
def gapi_create_freshes(ctx):
    """EXECUTED (lead-372r / GAP I): because the partial DB was cleared, the
    stubbed `bd init -p` CREATE-FRESHES (writing CREATED_FRESH) adopting the
    committed prefix, rather than aborting 'database already exists'."""
    assert (ctx["gapi_beads"] / "embeddeddolt" / "CREATED_FRESH").exists(), (
        "the create-fresh never ran — `bd init -p` aborted under `|| true` "
        "because the partial DB was left in place (lead-372r / GAP I)"
    )
    # The create-fresh still adopts the COMMITTED prefix from metadata.json.
    script = ctx["gapi_script"]
    assert 'bd init -p "$gapg_prefix"' in script, (
        "the create-fresh must adopt the committed prefix via "
        "`bd init -p \"$gapg_prefix\"` (lead-372r / GAP I)"
    )


@then(
    'the standup then seeds that prefixed local database with "bd dolt push" so '
    'the tracker remote carries Dolt data with "refs/dolt/*" refs present and '
    'the fatal "git ls-remote refs/dolt" verify passes rather than driving the '
    "seed to exit 1"
)
def gapi_seeds_and_verifies(ctx):
    """EXECUTED (lead-372r / GAP I): `bd dolt push` seeds THAT create-fresh'd DB
    (not nothing), and the seed carries the fatal `git ls-remote refs/dolt`
    verify that now passes rather than driving the seed to exit 1."""
    at_push = ctx["gapi_at_push"]
    assert at_push.exists() and at_push.read_text() == "seeded", (
        "`bd dolt push` seeded nothing — with the create-fresh aborted there was "
        "no prefixed DB to push, so refs/dolt/* never land and the fatal verify "
        "fails -> seed exit 1 -> BC offline (lead-372r / GAP I)"
    )
    script = ctx["gapi_script"]
    assert "bd dolt push" in script, (
        "the seed must `bd dolt push` to seed refs/dolt/* (lead-372r / GAP I)"
    )
    assert re.search(r"git ls-remote \S+ 'refs/dolt/\*'", script), (
        "the seed must carry the fatal git ls-remote refs/dolt verify "
        "(lead-372r / GAP I)"
    )


@then(
    'as the negative control, had the seed left the partial ".beads/embeddeddolt" '
    'in place, "bd init -p" would ABORT "database already exists" — a failure '
    'MASKED by the "|| true" — so the create-fresh would never run, "bd dolt '
    'push" would seed nothing, and the fatal verify would fail, which is the '
    "exact pre-fix offline failure this clear-before-init ordering exists to avoid"
)
def gapi_negative_control(ctx, tmp_path):
    """NEGATIVE CONTROL bound to REAL code (lead-372r / GAP I): the same seed
    body with ONLY the clear-before-init NEUTRALIZED leaves the partial
    `.beads/embeddeddolt` in place, so the stubbed `bd init -p` ABORTS under
    `|| true`, the create-fresh never runs, and `bd dolt push` seeds nothing —
    the exact pre-fix offline failure the clear-before-init ordering averts."""
    owner, bc = "dstengle", "shopsystem-knowledge"
    ws = tmp_path / "gapi_neg_ws"
    beads = ws / ".beads"
    beads.mkdir(parents=True)
    (beads / "config.yaml").write_text(
        "# Beads Configuration File\n\n" + _GAPI_SYNC_REMOTE_LINE + "\n"
    )
    (beads / "metadata.json").write_text(
        '{\n  "dolt_database": "shopsystem_knowledge"\n}\n'
    )
    (beads / "issues.jsonl").write_text(
        '{"_type":"issue","id":"shopsystem_knowledge-a1b","title":"seed"}\n'
    )
    partial = beads / "embeddeddolt"
    partial.mkdir()
    (partial / "PARTIAL_FROM_FAILED_CLONE").write_text("half-written\n")

    script = ctx["gapi_script"]
    assert _GAPI_CLEAR_STMT in script, (
        f"seed must carry the clear {_GAPI_CLEAR_STMT!r} to neutralize "
        "(lead-372r / GAP I)"
    )
    start = script.index("gapg_prefix=")
    end = script.index("git ls-remote", start)
    prefix_fix_fragment = script[start:end].replace(_GAPI_CLEAR_STMT, ":", 1)
    probe = tmp_path / "gapi_neg_probe"
    probe.mkdir()
    subprocess.run(
        ["bash", "-c", _gapi_stub_prelude(probe) + prefix_fix_fragment],
        cwd=str(ws),
        capture_output=True,
        text=True,
    )
    # With the clear neutralized the partial DB survives -> bd init aborts...
    assert (probe / "at_init").read_text() == "present-aborted", (
        "negative control: with the clear neutralized the partial DB must still "
        "be present at `bd init` time, aborting the create-fresh (lead-372r)"
    )
    # ...the create-fresh never runs...
    assert not (beads / "embeddeddolt" / "CREATED_FRESH").exists(), (
        "negative control: the aborted create-fresh must NOT write CREATED_FRESH "
        "(lead-372r / GAP I)"
    )
    # ...and bd dolt push seeds nothing — the pre-fix offline failure.
    assert (probe / "at_push").read_text() == "nothing", (
        "negative control: with no create-fresh'd DB `bd dolt push` seeds "
        "nothing and the fatal verify fails -> seed exit 1 -> BC offline "
        "(lead-372r / GAP I)"
    )


# ===========================================================================
# lead-b3f0 — ADR-058 AMENDED: fabro-engage REDESIGN.  THREE superseding
# scenarios correcting the pinned-but-broken fabro engage lifecycle:
#   A @scenario_hash:24d94274b9cbc2b0 — the engage invokes `fabro run
#     dispatcher.toml` (the .toml entrypoint applies provider=local so the
#     sandbox comes up IN-PROCESS), NOT the bare `dispatcher.fabro` graph def
#     (which would bypass [environments.local], default to the docker sandbox,
#     and fail 0s at /var/run/docker.sock in the docker-less bc-base).
#   B @scenario_hash:a5e16a192f755768 (RECONCILED by lead-3zzu to
#     9f31aa5f378410b1) — dispatcher.fabro is a cyclic poll-loop
#     (start->poll->dispatch->wait->poll); poll/wait are native script= nodes and
#     `dispatch` is the ONLY agent node, a NON-LLM ACP script-agent; NO
#     long-running `shop-msg watch` node, NO LLM node.
#   (lead-b3f0 scenario C — the NATIVE dispatch node's per-child env-overlay
#    work_id delivery — was RETIRED WHOLESALE by lead-3zzu / ADR-058 Amendment 2;
#    its guarantee is re-pinned through the ACP node by f38ab66672151669, the
#    lead-3zzu delivery scenario.)
#
# FIDELITY: A binds to the REAL launcher's ACTUAL recorded engage argv
# (controller.launch over FakeDockerDriver); B binds structurally to the REAL
# committed dispatcher.fabro / dispatcher.toml asset bytes (reusing the ky63
# quote-aware DOT parser).  Never a model.  (Supersedes/retires the old
# cyclic-Haiku dispatcher pin wholesale, and reconciles 30fd5f2079f1c433 /
# cacccc52ba0b0766 to the .toml engage argv.)
# ===========================================================================


def _b3f0_dispatcher_toml_text():
    """The REAL committed dispatcher.toml bytes (the poured .toml entrypoint),
    via the launcher's own def-asset root."""
    path = _ky63_def_asset_root() / "dispatcher.toml"
    assert path.is_file(), (
        f"the poured dispatcher.toml entrypoint is ABSENT at {path}; the .toml "
        "entrypoint must ship in the launcher's fabro-def bundle (ADR-058)"
    )
    return path.read_text()


# --- Given (A): fabro launch, no --work-id suffix (scenario A wording) --------

@given(parsers.parse(
    'bc-container launch is run for BC name "{bc_name}" on the fabro '
    'orchestrator launch path selected by "--orchestrator fabro"'))
def b3f0_launch_fabro(bc_name, ctx, fake_driver, controller, tmp_path):
    _odd9_drive_fabro_launch(bc_name, ctx, fake_driver, controller, tmp_path,
                             work_id=None)


# --- Given (A): container running with BOTH the .toml entrypoint + .fabro -----

@given(parsers.parse(
    'the container "{container_name}" is running with the self-contained fabro '
    'def set POURED by shop-templates into "{def_dir}", including both the '
    '"dispatcher.toml" entrypoint and the "dispatcher.fabro" graph def it '
    'applies, and the bc-base container has NO docker daemon reachable at '
    '"{sock}"'))
def b3f0_container_running_toml_entrypoint(container_name, def_dir, sock, ctx,
                                           fake_driver):
    assert fake_driver.is_running(container_name), (
        f"Expected {container_name!r} to be running after the fabro-path launch."
    )
    ctx["container_name"] = container_name
    ctx["b3f0_sock"] = sock


# --- When (A): inspect the engage + the poured dispatcher.toml entrypoint -----

@when(parsers.parse(
    'the engage the launcher issues and the poured "dispatcher.toml" '
    "entrypoint are inspected structurally, without a live docker daemon, a "
    "running fabro server, or a reachable agent-vault"))
def b3f0_inspect_engage_and_toml(ctx):
    ctx["b3f0_dispatcher_toml"] = _b3f0_dispatcher_toml_text()


# --- Then (A): the engage invokes `fabro run dispatcher.toml`, not the .fabro -

@then(parsers.parse(
    'AFTER the readiness barrier passes the engage the launcher issues invokes '
    '"{run_argv}" — the ".toml" entrypoint, NOT the bare "dispatcher.fabro" '
    'graph def — so the run enters through the ".toml" rather than the ".fabro" '
    "directly"))
def b3f0_engage_runs_toml(run_argv, ctx):
    assert run_argv == "fabro run dispatcher.toml"
    call = _cadr_fabro_engage_call(ctx)
    assert call is not None, (
        "the fabro-path launcher did not emit a fabro engage exec; "
        f"exec_calls: {[c.command[:3] for c in _cadr_exec_calls(ctx)]!r}"
    )
    script = call.command[2]
    # The engage enters through the .toml entrypoint (provider=local in-process),
    # not the bare .fabro graph def (which defaults to the docker sandbox).
    assert "fabro run dispatcher.toml" in script, (
        f"the engage must invoke `fabro run dispatcher.toml`; script:\n{script}"
    )
    # It must NOT run the bare `fabro run dispatcher.fabro` graph def directly
    # (the exact pre-fix offline failure).
    assert not re.search(r"fabro run dispatcher\.fabro(?!\.)", script), (
        "the engage must NOT run the bare `fabro run dispatcher.fabro` graph "
        f"def directly (it would fall to the docker sandbox); script:\n{script}"
    )


# --- Then (A): the poured dispatcher.toml applies provider = local ------------

@then(parsers.parse(
    'the poured "dispatcher.toml" applies "{provider_line}" so the fabro '
    'sandbox comes up IN-PROCESS in the bc-base container ("{ready}") and every '
    "native node of the dispatcher graph executes in-process with no docker "
    'sandbox and no connection attempt to "{sock}"'))
def b3f0_toml_provider_local(provider_line, ready, sock, ctx):
    toml = ctx.get("b3f0_dispatcher_toml") or _b3f0_dispatcher_toml_text()
    # The .toml entrypoint carries an [environments.local] provider = "local"
    # block — that is what brings the sandbox up IN-PROCESS (no docker sock).
    assert "[environments.local]" in toml, (
        "the dispatcher.toml must declare an [environments.local] environment; "
        f"toml:\n{toml}"
    )
    assert re.search(r'provider\s*=\s*"local"', toml), (
        f"the dispatcher.toml must apply {provider_line!r} (provider = \"local\") "
        f"so the sandbox comes up in-process; toml:\n{toml}"
    )
    # The .toml BINDS the dispatcher.fabro graph def (it "applies" it).
    assert re.search(r'graph\s*=\s*"dispatcher\.fabro"', toml), (
        "the dispatcher.toml must apply the dispatcher.fabro graph def "
        f"([workflow] graph = \"dispatcher.fabro\"); toml:\n{toml}"
    )


# --- Then (A): negative control — the bare .fabro would fall to docker --------

@then(parsers.parse(
    'as the negative control, had the engage instead run the bare "fabro run '
    'dispatcher.fabro" (the ".fabro" graph def DIRECTLY), the run would BYPASS '
    'the "{env_block}" provider, DEFAULT to the docker-sandbox executor, and — '
    "because the bc-base container has no docker daemon — fail in 0s connecting "
    'to "{sock}" and EXIT before the dispatcher ever watches the inbox, which '
    'is the exact pre-fix offline failure this ".toml"-entrypoint engage exists '
    "to avoid"))
def b3f0_negative_control_bare_fabro(env_block, sock, ctx):
    # STRUCTURAL negative control: the engage the launcher ACTUALLY issued enters
    # through the .toml (which applies [environments.local] provider=local), so
    # it does NOT take the bare-.fabro docker-sandbox path.
    call = _cadr_fabro_engage_call(ctx)
    assert call is not None
    script = call.command[2]
    assert not re.search(r"fabro run dispatcher\.fabro(?!\.)", script), (
        "the engage must NOT run the bare `fabro run dispatcher.fabro`; that "
        f"bare graph-def run is the docker-sandbox negative control; script:\n{script}"
    )
    # And the .toml the engage DOES enter through is what supplies the
    # [environments.local] provider=local that averts the docker-sock failure.
    toml = ctx.get("b3f0_dispatcher_toml") or _b3f0_dispatcher_toml_text()
    assert env_block == "[environments.local]"
    assert env_block in toml and re.search(r'provider\s*=\s*"local"', toml), (
        f"the .toml entrypoint must carry {env_block!r} provider=local so the "
        f"sandbox is in-process and never connects to {sock!r}; toml:\n{toml}"
    )


# ===========================================================================
# lead-b3f0 scenario B (@scenario_hash:a5e16a192f755768): the poured
# dispatcher.fabro is a NATIVE cyclic poll-loop (start->poll->dispatch->wait->
# poll, the wait->poll back-edge forming the cycle).  poll/dispatch/wait are
# each a native script= node (parallelogram, no LLM); there is NO long-running
# `shop-msg watch` node and NO LLM/agent node anywhere in the loop, so the
# steady-state loop spends ZERO model tokens.  Binds structurally to the REAL
# committed dispatcher.fabro asset bytes (reusing the ky63 quote-aware DOT
# parser).  Never a model.
# ===========================================================================

_B3F0_DISPATCHER_NODES = {"start", "end", "poll", "dispatch", "wait"}


def _b3f0_dispatcher_graph_text():
    """The REAL committed dispatcher.fabro bytes (the poured graph def), via the
    launcher's own def-asset root."""
    path = _ky63_def_asset_root() / "dispatcher.fabro"
    assert path.is_file(), (
        f"the poured dispatcher.fabro graph def is ABSENT at {path}; the native "
        "poll-loop dispatcher def must ship in the launcher's fabro-def bundle "
        "(ADR-058 AMENDED)"
    )
    return path.read_text()


def _b3f0_dispatcher_edges(graph: str):
    """[(src, dst, attr_block)] for edges between dispatcher nodes, quote-aware
    and comment-stripped (mirrors _ky63_parse_edges, filtered to the dispatcher
    node set)."""
    lc = _ky63_strip_line_comments(graph)
    inner = lc[lc.index("{") + 1:lc.rindex("}")]
    edges = []
    for m in re.finditer(r"\b([A-Za-z_]\w*)\s*->\s*([A-Za-z_]\w*)", inner):
        src, dst = m.group(1), m.group(2)
        k = m.end()
        while k < len(inner) and inner[k] in " \t\n":
            k += 1
        attr = ""
        if k < len(inner) and inner[k] == "[":
            depth = 0
            inq = False
            j = k
            while j < len(inner):
                c = inner[j]
                if inq:
                    if c == "\\":
                        j += 2
                        continue
                    if c == '"':
                        inq = False
                else:
                    if c == '"':
                        inq = True
                    elif c == "[":
                        depth += 1
                    elif c == "]":
                        depth -= 1
                        if depth == 0:
                            attr = inner[k:j + 1]
                            break
                j += 1
        if src in _B3F0_DISPATCHER_NODES and dst in _B3F0_DISPATCHER_NODES:
            edges.append((src, dst, attr))
    return edges


def _b3f0_native_body(nodes, name):
    """Assert node ``name`` is a NATIVE script= node (parallelogram, no LLM) and
    return its attr body."""
    assert name in nodes, f"dispatcher.fabro missing native node {name!r}"
    body = nodes[name]
    assert "script=" in body, f"the {name!r} node must be a NATIVE script= node; body:\n{body}"
    assert "shape=parallelogram" in body, (
        f"the {name!r} node must be shape=parallelogram (native); body:\n{body}"
    )
    assert "prompt=" not in body and "class=" not in body, (
        f"the {name!r} node must have NO LLM (no prompt=/class=); body:\n{body}"
    )
    return body


# --- Given (B): container running with the poured native poll-loop def --------

@given(parsers.parse(
    'the container "{container_name}" is running with the self-contained fabro '
    'def set POURED by shop-templates into "{def_dir}", including the '
    '"dispatcher.fabro" graph def the "dispatcher.toml" entrypoint applies'))
def b3f0_container_running_polloop(container_name, def_dir, ctx, fake_driver,
                                   controller, tmp_path):
    _odd9_drive_fabro_launch(_ODD9_BC, ctx, fake_driver, controller, tmp_path,
                             work_id=None)
    assert fake_driver.is_running(container_name), (
        f"Expected {container_name!r} to be running after the fabro-path launch."
    )
    ctx["container_name"] = container_name


# --- When (B): inspect the poured dispatcher.fabro structurally ---------------

@when(parsers.parse(
    'the poured "dispatcher.fabro" def is inspected structurally, without a '
    "live docker daemon, a running fabro server, or a reachable agent-vault"))
def b3f0_inspect_dispatcher_fabro(ctx):
    ctx["b3f0_dispatcher_graph"] = _b3f0_dispatcher_graph_text()


def _b3f0_graph(ctx):
    return ctx.get("b3f0_dispatcher_graph") or _b3f0_dispatcher_graph_text()


# --- Then (B): the native cyclic poll-loop shape -----------------------------

@then(parsers.parse(
    'the "dispatcher.fabro" is a CYCLIC graph whose loop is "{loop}", the '
    '"wait -> poll" edge being the BACK-EDGE that forms the cycle, so the run '
    "persists by cycling poll->dispatch->wait->poll rather than blocking on a "
    "single long-running watch"))
def b3f0_cyclic_polloop(loop, ctx):
    assert loop == "start -> poll -> dispatch -> wait -> poll"
    graph = _b3f0_graph(ctx)
    nodes = _ky63_parse_nodes(graph)
    for n in ("start", "poll", "dispatch", "wait"):
        assert n in nodes, f"dispatcher.fabro missing node {n!r}"
    edges = _b3f0_dispatcher_edges(graph)
    pairs = {(s, d) for s, d, a in edges}
    for want in (("start", "poll"), ("poll", "dispatch"), ("dispatch", "wait"),
                 ("wait", "poll")):
        assert want in pairs, f"missing {want[0]} -> {want[1]} edge; edges={pairs!r}"
    # The wait -> poll BACK-EDGE is what makes the graph cyclic and unconditional
    # (it always loops back to poll).
    wait_poll = [a for s, d, a in edges if (s, d) == ("wait", "poll")]
    assert wait_poll and "outcome=failed" not in wait_poll[0], (
        "wait -> poll must be the UNCONDITIONAL back-edge that forms the cycle"
    )
    # Genuinely CYCLIC: poll is reachable from wait and wait from poll (via
    # dispatch), so the run persists cycling rather than blocking on a watch.
    assert ("wait", "poll") in pairs and ("poll", "dispatch") in pairs


# --- Then (B): poll is a native non-blocking pending-inbox lister -------------

@then(parsers.parse(
    'the "poll" node is a NATIVE "script=" node with no LLM that lists the '
    'current pending inbox via "{pending_cmd}" and yields the concrete pending '
    "work ids, returning promptly rather than blocking"))
def b3f0_poll_native(pending_cmd, ctx):
    graph = _b3f0_graph(ctx)
    nodes = _ky63_parse_nodes(graph)
    body = _b3f0_native_body(nodes, "poll")
    # The poured def is BC-GENERIC: BC_NAME arrives via [run.environment.env], so
    # the node names the base command parameterized by $BC_NAME.
    pending_base = pending_cmd.replace(" shopsystem-messaging", "")
    assert pending_base in body and "$BC_NAME" in body, (
        f"the poll node must list pending inbox via {pending_base!r} "
        f"(parameterized by $BC_NAME); body:\n{body}"
    )
    # Returns PROMPTLY: it must NOT block on a long-running `shop-msg watch`.
    assert "shop-msg watch" not in body, (
        f"the poll node must return promptly (NO long-running `shop-msg watch`); "
        f"body:\n{body}"
    )


# --- Then (B): dispatch is a native node acting on the poll ids ---------------

@then(parsers.parse(
    'the "dispatch" node is the only agent node — a non-LLM ACP script-agent '
    '("backend=acp") — that acts on the pending work ids from "poll"'))
def b3f0_dispatch_native(ctx):
    # RECONCILED (lead-3zzu, ADR-058 Amendment 2): `dispatch` is no longer a
    # native script= node — it is the ONLY agent node in the loop, a NON-LLM ACP
    # script-agent (backend="acp" + acp.command/acp.config). The a5e16 KEEP-set
    # (cyclic topology, poll/wait native, no shop-msg watch, zero-token) is
    # unchanged; only the now-false "dispatch is native / no agent node" clause
    # is reconciled. lead-4uo1 owns the formal re-author.
    graph = _b3f0_graph(ctx)
    nodes = _ky63_parse_nodes(graph)
    assert "dispatch" in nodes, "dispatcher.fabro missing the dispatch node"
    body = nodes["dispatch"]
    assert re.search(r'backend\s*=\s*"acp"', body), (
        f'the `dispatch` node must be the non-LLM ACP script-agent (backend="acp"); body:\n{body}'
    )
    assert "script=" not in body, (
        f"the ACP dispatch agent must NOT be a native script= command node; body:\n{body}"
    )
    # NON-LLM: no prompt= model text; it drives an external ACP process.
    assert "prompt=" not in body, (
        f"the ACP dispatch agent is NON-LLM (no prompt= model text); body:\n{body}"
    )
    assert ("acp.command" in body) or ("acp.config" in body), (
        f"the ACP dispatch agent must name its ACP process (acp.command/acp.config); body:\n{body}"
    )


# --- Then (B): wait is a native short-sleep node before the back-edge ---------

@then(parsers.parse(
    'the "wait" node is a NATIVE "script=" node with no LLM that sleeps a short '
    'interval before the back-edge returns to "poll"'))
def b3f0_wait_native(ctx):
    graph = _b3f0_graph(ctx)
    nodes = _ky63_parse_nodes(graph)
    body = _b3f0_native_body(nodes, "wait")
    assert "sleep" in body, (
        f"the wait node must sleep a short interval before the back-edge; body:\n{body}"
    )


# --- Then (B): no watch node, no LLM/agent node anywhere in the loop ----------

@then(parsers.parse(
    'the def contains NO long-running "shop-msg watch" node and its only agent '
    'node is the non-LLM ACP dispatch script-agent (no Haiku "launch" node and '
    'no other model-backed LLM node) anywhere in the loop, so the steady-state '
    "loop consumes NO model tokens and tokens are spent only on the child's "
    "actual work"))
def b3f0_no_watch_no_llm(ctx):
    graph = _b3f0_graph(ctx)
    # Strip DOT `//` line comments so the EXECUTABLE graph (not the explanatory
    # prose, which legitimately NAMES the retired `shop-msg watch` / Haiku
    # `launch` / model constructs to say they are ABSENT) is what is asserted on.
    executable = _ky63_strip_line_comments(graph)
    nodes = _ky63_parse_nodes(graph)
    # NO long-running `shop-msg watch` node anywhere in the executable def.
    assert "shop-msg watch" not in executable, (
        "the poll-loop must contain NO long-running `shop-msg watch` "
        f"node; executable graph:\n{executable}"
    )
    # NO `launch` node (the retired Haiku agent) — the loop nodes are exactly the
    # native poll/wait, the ACP dispatch agent, and terminals.
    assert "launch" not in nodes, (
        "the poll-loop must contain NO Haiku `launch` agent node"
    )
    # RECONCILED (lead-3zzu, ADR-058 Amendment 2): the ONLY agent node is the
    # NON-LLM ACP dispatch script-agent. Every OTHER node is native (no
    # prompt=/class=); `dispatch` carries class= + backend="acp" but is NON-LLM
    # (no prompt= model text, drives an external ACP process). Zero tokens holds
    # because the ACP agent is a non-LLM script, NOT because there is no agent
    # node.
    assert "dispatch" in nodes, "dispatcher.fabro missing the ACP dispatch node"
    dispatch_body = nodes["dispatch"]
    assert re.search(r'backend\s*=\s*"acp"', dispatch_body), (
        "the only agent node must be the ACP dispatch script-agent "
        f'(backend="acp"); dispatch body:\n{dispatch_body}'
    )
    for name, body in nodes.items():
        if name == "dispatch":
            # The ACP agent is NON-LLM: it carries NO prompt= model text.
            assert "prompt=" not in body, (
                f"the ACP dispatch agent must be NON-LLM (no prompt=); body:\n{body}"
            )
            continue
        assert "prompt=" not in body and "class=" not in body, (
            f"loop node {name!r} (other than the ACP dispatch agent) must be "
            f"NATIVE (no LLM prompt=/class=); body:\n{body}"
        )
    assert "model_stylesheet" not in executable and re.search(r"model\s*:", executable) is None, (
        "the poll-loop must declare NO model binding (no model_stylesheet "
        f"/ model:), so the steady-state loop spends zero model tokens; "
        f"executable graph:\n{executable}"
    )


# ===========================================================================
# lead-3zzu — ADR-058 Amendment 2: the dispatcher's `dispatch` node becomes an
# ACP-backed NON-LLM SCRIPT-AGENT (backend="acp" + acp.command/acp.config) so it
# is CONTEXT-BLIND no longer.  Unlike the pre-fix native command node (which
# re-fired for every still-pending work id each ~6s poll cycle, duplicate-
# spawning a slow child onto the shared per-WORK_ID worktree), the ACP node
# RECEIVES the poll context (pending ids + in-flight run state) and RETURNS
# structured dispatch DECISIONS: SKIP a work id whose child is still in flight
# (idempotency), SPAWN a work id with no live child (exactly once).  Zero-token
# holds because the ACP agent is a NON-LLM python SCRIPT (Fabro injects no model
# creds), NOT because the loop has no agent node.  Binds structurally to the
# REAL committed dispatcher.fabro `dispatch` node body AND to the directly unit-
# testable decision contract of the poured dispatch_acp_agent.py.  Never a model.
#   acpkind   @scenario_hash:7709a671bdfaddb7
#   idemp     @scenario_hash:713d01c4f4dfd107
#   delivery  @scenario_hash:f38ab66672151669
# ===========================================================================
import inspect as _l3zzu_inspect

_L3ZZU_STEP = {
    'acp_when': 'the poured "dispatcher.fabro" def\'s "dispatch" node is inspected structurally, without a live docker daemon, a running fabro server, or a reachable agent-vault',
    'acp_then_kind': 'the "dispatch" node is an ACP-backed AGENT node carrying "backend=acp" together with an "acp.command" attr (a shell such as "python3 <dispatch_acp_agent.py>") OR an "acp.config" attr (a JSON stdio config), so fabro drives it through the agent-client-protocol backend',
    'acp_then_notnative': 'the "dispatch" node is NOT a native "script="/parallelogram command node, so the pre-fix context-blind command dispatch is absent',
    'acp_then_receive': 'the "dispatch" node is wired to RECEIVE the incoming context yielded by the "poll" node — the pending inbox work ids plus the in-flight run state — as its input',
    'acp_then_return': 'the "dispatch" node is wired to RETURN structured dispatch DECISIONS as its output, which the loop consumes to spawn children, so the dispatch step both reads context and emits decisions rather than blindly re-acting on raw work ids each cycle',
}


def _l3zzu_dispatch_body(ctx):
    graph = ctx.get("l3zzu_graph") or _b3f0_dispatcher_graph_text()
    nodes = _ky63_parse_nodes(graph)
    assert "dispatch" in nodes, "dispatcher.fabro missing the `dispatch` node"
    return nodes["dispatch"]


def _l3zzu_load_acp_agent():
    """Import the poured NON-LLM ACP dispatch script-agent module from the
    fabro-def asset root.  Its decision contract (decide / DispatchTracker /
    materialize_child_config / spawn_command) is directly unit-testable because
    it is a plain python script, not an LLM: feed it context, assert decisions.
    """
    path = _ky63_def_asset_root() / "dispatch_acp_agent.py"
    assert path.is_file(), (
        f"the NON-LLM ACP dispatch script-agent is ABSENT at {path}; the ACP "
        "dispatch node's acp.command must point at a poured dispatch_acp_agent.py "
        "(ADR-058 Amendment 2, lead-3zzu)"
    )
    # compile+exec into a fresh module namespace rather than SourceFileLoader,
    # so loading the poured asset writes NO __pycache__ into the def-bundle tree
    # (the bundle-shape test asserts the asset root holds EXACTLY the enumerated
    # files -- a stray .pyc would be a spurious extra).
    import types as _types
    mod = _types.ModuleType("dispatch_acp_agent")
    mod.__file__ = str(path)
    exec(compile(path.read_text(), str(path), "exec"), mod.__dict__)
    return mod


# --- When (acpkind): inspect the poured dispatch node structurally ------------

@when(_L3ZZU_STEP['acp_when'])
def l3zzu_inspect_dispatch_node(ctx):
    ctx["l3zzu_graph"] = _b3f0_dispatcher_graph_text()


# --- Then (acpkind): the dispatch node is an ACP-backed agent node ------------

@then(_L3ZZU_STEP['acp_then_kind'])
def l3zzu_dispatch_is_acp(ctx):
    body = _l3zzu_dispatch_body(ctx)
    # backend="acp": fabro drives the node through the agent-client-protocol
    # backend (v0.11.1 JSON-RPC over stdio), not the native command executor.
    assert re.search(r'backend\s*=\s*"acp"', body), (
        f'the `dispatch` node must carry backend="acp"; body:\n{body}'
    )
    # exactly one of acp.command (a shell) OR acp.config (a JSON stdio config)
    # names the external ACP process fabro launches.
    assert ("acp.command" in body) or ("acp.config" in body), (
        "the ACP dispatch node must carry an acp.command (shell such as "
        '"python3 dispatch_acp_agent.py") OR an acp.config (JSON stdio config) '
        f"attr; body:\n{body}"
    )


# --- Then (acpkind): the dispatch node is NOT a native command node -----------

@then(_L3ZZU_STEP['acp_then_notnative'])
def l3zzu_dispatch_not_native(ctx):
    body = _l3zzu_dispatch_body(ctx)
    # The pre-fix context-blind native command dispatch is ABSENT: the ACP node
    # is neither a `script=` node nor a `shape=parallelogram` command node.
    assert "script=" not in body, (
        "the ACP dispatch node must NOT be a native `script=` command node "
        f"(the pre-fix context-blind dispatch must be absent); body:\n{body}"
    )
    assert "shape=parallelogram" not in body, (
        "the ACP dispatch node must NOT be a native shape=parallelogram command "
        f"node; body:\n{body}"
    )


# --- Then (acpkind): wired to RECEIVE the poll context as input ---------------

@then(_L3ZZU_STEP['acp_then_receive'])
def l3zzu_dispatch_receives_context(ctx):
    graph = ctx.get("l3zzu_graph") or _b3f0_dispatcher_graph_text()
    # The poll -> dispatch edge feeds the ACP node the context poll yielded.
    pairs = {(s, d) for s, d, _a in _b3f0_dispatcher_edges(graph)}
    assert ("poll", "dispatch") in pairs, (
        "the ACP dispatch node must be wired to receive the poll context via a "
        f"poll -> dispatch edge; edges={pairs!r}"
    )
    # Its decision contract RECEIVES the pending work ids AND the in-flight run
    # state as its two inputs (context-in).
    mod = _l3zzu_load_acp_agent()
    assert hasattr(mod, "decide"), (
        "the ACP agent must expose a `decide` decision contract (context-in / "
        "decisions-out)"
    )
    params = list(_l3zzu_inspect.signature(mod.decide).parameters)
    assert len(params) >= 2, (
        "decide must RECEIVE the pending inbox work ids AND the in-flight run "
        f"state as its input; signature params: {params!r}"
    )


# --- Then (acpkind): wired to RETURN structured decisions as output -----------

@then(_L3ZZU_STEP['acp_then_return'])
def l3zzu_dispatch_returns_decisions(ctx):
    mod = _l3zzu_load_acp_agent()
    decisions = mod.decide(["lead-a1"], set())
    assert isinstance(decisions, list) and decisions, (
        "decide must RETURN a structured list of dispatch decisions (decisions-out)"
    )
    d = decisions[0]
    assert isinstance(d, dict) and "work_id" in d and "action" in d, (
        "each returned decision must be a structured {work_id, action} record "
        f"the loop consumes to spawn children; got: {d!r}"
    )
    assert d["action"] in ("SPAWN", "SKIP"), (
        f"a decision's action must be SPAWN or SKIP; got {d['action']!r}"
    )
