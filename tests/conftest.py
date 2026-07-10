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


@pytest.fixture
def fake_github():
    return FakeGitHubDriver()


@pytest.fixture
def fake_git():
    return FakeGitDriver()


from bc_launcher.controller import (
    AGENT_VAULT_MITM_PROXY_PORT,
    AGENT_VAULT_PLACEHOLDER_TOKEN,
    CONTAINER_CLAUDE_CREDENTIALS_PATH,
    DEFAULT_AGENT_VAULT_BROKER,
)


from bc_launcher.controller import (
    AGENT_VAULT_ADDR_ENV,
    AGENT_VAULT_TOKEN_ENV,
    AGENT_VAULT_VAULT_ENV,
    CONTAINER_BROKER_CA_PATH,
)


_SRC_ROOT = Path(__file__).resolve().parent.parent / "src"


_REPO_ROOT = Path(__file__).resolve().parent.parent


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


_VWIB_REPO_ROOT = Path(__file__).resolve().parent.parent


from bc_launcher.cli import build_parser as _cadr_build_parser


from bc_launcher.controller import (
    AGENT_TMUX_SESSION as _CADR_AGENT_SESSION,
    LAUNCH_PATH_FABRO as _CADR_LAUNCH_PATH_FABRO,
    LAUNCH_PATH_TMUX as _CADR_LAUNCH_PATH_TMUX,
    _fabro_server_start_argv as _cadr_server_start_argv,
)


from bc_launcher.controller import (  # noqa: E402
    FABRO_SERVER_SETTINGS_CONTAINER_PATH as _ODD9_SERVER_SETTINGS_PATH,
    FABRO_SETTINGS_CONTAINER_PATH as _ODD9_PROJECT_SETTINGS_PATH,
    FABRO_DEF_CONTAINER_DIR as _ODD9_DEF_DIR,
)


import inspect as _l3zzu_inspect


# ---------------------------------------------------------------------------
# Step-definition modules: discovered dynamically. Drop a module in
# tests/steps/ and it is registered — no manual list to forget.
# Step defs must NOT be added to this file (enforced by tests/steps/test_step_hygiene.py).
# ---------------------------------------------------------------------------
from pathlib import Path as _Path

pytest_plugins = sorted(
    f"tests.steps.{p.stem}"
    for p in (_Path(__file__).parent / "steps").glob("*.py")
    if not p.stem.startswith(("_", "test_"))
)
