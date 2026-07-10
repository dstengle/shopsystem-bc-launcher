"""Support helpers/constants: manifest (extracted from tests/conftest.py).

Plain imported module (NOT a pytest plugin). Domain boundaries are
organizational; step modules import what they reference from here.
"""
from __future__ import annotations

from bc_launcher.manifest import ManifestController
from pathlib import Path
from tests.fake_git_driver import FakeGitDriver
from tests.fake_github_driver import FakeGitHubDriver
import yaml


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
