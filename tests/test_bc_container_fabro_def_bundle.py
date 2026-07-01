"""
Unit tests pinning the self-contained fabro loop def-bundle delivery
(lead-h2bj — S2 def-bundle delivery, ADR-051).

Background
----------
shopsystem-bc-launcher ships the bc-shop Implementer->Reviewer loop fabro
def (ADR-051) as packaged asset files under
``src/bc_launcher/assets/fabro-def/`` and PLACES them into every launched
container at ``/workspace/.fabro/`` so the container carries a
self-contained fabro def runnable FROM THE DEF ALONE — nothing fetched at
run time.

These tests guard the delivery so a future refactor cannot silently drop a
def file, mangle its bytes, break the placement wiring, or leak a real
credential into the fabro vault.  They are plain unit tests (NO
``@scenario_hash`` tag): the block-only scenario pin is a SEPARATE companion
dispatch (lead-ky63, @scenario_hash:2dfefe2ba81e418d) and is NOT authored
here.

Invariants pinned
-----------------
* All 15 def-root files are present as launcher assets, verbatim-shaped
  (acceptance criterion 0).
* ``bc-container launch`` places the 15 files into the container at
  ``/workspace/.fabro/`` (acceptance criterion 1).
* The def's native fabro vault (``vaults/default/secrets.json``) holds ONLY
  ``__PLACEHOLDER__`` for every slot — no real credential (ADR-049,
  acceptance criterion 3).
* Every ``prompt_file=`` node reference in ``workflow.fabro`` resolves to a
  present node asset (structural "runnable-from-the-def-alone" check).
* The placement is ADDITIVE: the tmux launch default and the prior
  provisioning steps are unchanged (acceptance criterion 4).

The launch tests use FakeDockerDriver and require no live Docker.
"""
from __future__ import annotations

import base64
import json
import re
import tomllib
from pathlib import Path

from bc_launcher.controller import (
    AGENT_CONTAINER_USER,
    BcContainerController,
    CONTAINER_WORKSPACE,
    FABRO_DEF_CONTAINER_DIR,
    FABRO_DEF_FILES,
    _fabro_def_asset_root,
    _fabro_def_install_script,
    _load_fabro_def_files,
)
from tests.fake_driver import FakeDockerDriver


BC_NAME = "shopsystem-messaging"

# The 15 def-root-relative paths the bundle MUST ship (acceptance criterion
# 0).  Enumerated independently of the source constant so a change to
# FABRO_DEF_FILES that drops or renames a file fails this test loudly.
EXPECTED_DEF_FILES = (
    "workflow.fabro",
    "workflow.toml",
    "project.toml",
    "vaults/default/secrets.json",
    "nodes/bc-implementer.md",
    "nodes/bc-review.md",
    "nodes/bc-reviewer.md",
    "nodes/bc-router.md",
    "nodes/bc-sufficiency-check.md",
    "nodes/integrating-to-main.md",
    "nodes/subagent-driven-development.md",
    "nodes/test-driven-development.md",
    "nodes/using-git-worktrees.md",
    "nodes/work-done-gate.md",
    "nodes/writing-plans-bdd.md",
)


# ---------------------------------------------------------------------------
# Asset-on-disk / verbatim-shape invariants (criterion 0)
# ---------------------------------------------------------------------------

def test_all_fifteen_def_files_present_as_launcher_assets():
    """All 15 def-root files ship as launcher assets at the correct paths."""
    root = _fabro_def_asset_root()
    for rel in EXPECTED_DEF_FILES:
        path = root / rel
        assert path.is_file(), f"missing def-bundle asset: {rel} (expected {path})"

    # Exactly 15 files under the asset root — no thinner and no extra bundle.
    present = {
        str(p.relative_to(root)).replace("\\", "/")
        for p in root.rglob("*")
        if p.is_file()
    }
    assert present == set(EXPECTED_DEF_FILES), (
        "def-bundle asset set does not match the expected 15 files.\n"
        f"unexpected extras: {present - set(EXPECTED_DEF_FILES)}\n"
        f"missing: {set(EXPECTED_DEF_FILES) - present}"
    )


def test_def_files_constant_matches_the_expected_fifteen():
    """FABRO_DEF_FILES enumerates exactly the 15 def-root files."""
    assert tuple(FABRO_DEF_FILES) == EXPECTED_DEF_FILES


def test_loader_reads_all_fifteen_files_as_bytes():
    files = _load_fabro_def_files()
    assert set(files) == set(EXPECTED_DEF_FILES)
    for rel, data in files.items():
        assert isinstance(data, (bytes, bytearray)), rel
        assert len(data) > 0, f"def-bundle asset is empty: {rel}"


def test_toml_and_fabro_assets_parse():
    """workflow.toml / project.toml parse as valid TOML; workflow.fabro is a
    non-empty DOT digraph body (structural parse check, since fabro is not on
    the launcher host)."""
    root = _fabro_def_asset_root()
    for name in ("workflow.toml", "project.toml"):
        with (root / name).open("rb") as fh:
            tomllib.load(fh)  # raises on invalid TOML
    fabro = (root / "workflow.fabro").read_text()
    assert "digraph BcShopLoop {" in fabro
    assert fabro.rstrip().endswith("}")


# ---------------------------------------------------------------------------
# Native-vault invariant (ADR-049, criterion 3)
# ---------------------------------------------------------------------------

def test_vault_is_placeholder_only_and_valid_json():
    """vaults/default/secrets.json is valid JSON holding ONLY __PLACEHOLDER__
    for every slot — no real credential (ADR-049)."""
    root = _fabro_def_asset_root()
    text = (root / "vaults/default/secrets.json").read_text()
    doc = json.loads(text)  # raises on invalid JSON
    assert doc, "vault must declare at least one slot"
    for slot, entry in doc.items():
        assert entry.get("value") == "__PLACEHOLDER__", (
            f"vault slot {slot!r} must hold __PLACEHOLDER__ (ADR-049), "
            f"got {entry.get('value')!r}"
        )


def test_no_real_credential_token_leaks_into_the_vault():
    """Defensive: no provider-token-shaped literal appears anywhere in the
    vault asset (guards a future edit from populating a real secret)."""
    root = _fabro_def_asset_root()
    text = (root / "vaults/default/secrets.json").read_text()
    suspicious = re.findall(
        r"(sk-[A-Za-z0-9_-]{12,}|ghp_[A-Za-z0-9]{12,}|github_pat_[A-Za-z0-9_]{12,})",
        text,
    )
    assert not suspicious, f"real-token-shaped literal found in vault: {suspicious}"


# ---------------------------------------------------------------------------
# Runnable-from-the-def-alone structural check
# ---------------------------------------------------------------------------

def test_workflow_prompt_file_references_all_resolve_to_present_nodes():
    """Every ``prompt_file="nodes/..."`` reference in workflow.fabro resolves
    to a present node asset — nothing fetched at run time."""
    root = _fabro_def_asset_root()
    graph = (root / "workflow.fabro").read_text()
    refs = sorted(set(re.findall(r'prompt_file="([^"]+)"', graph)))
    assert refs, "expected at least one prompt_file= node reference"
    for ref in refs:
        assert (root / ref).is_file(), (
            f"workflow.fabro references {ref!r} but that node asset is absent"
        )


# ---------------------------------------------------------------------------
# Placement-into-container wiring (criterion 1) + additive (criterion 4)
# ---------------------------------------------------------------------------

def _make_credential_home(tmp_path: Path) -> Path:
    home = tmp_path / "fake_home"
    home.mkdir()
    (home / ".claude").mkdir()
    (home / ".claude" / ".claude.json").write_text("{}")
    (home / ".config" / "gh").mkdir(parents=True)
    (home / ".gitconfig").write_text("")
    return home


def _make_manifest(tmp_path: Path, bc_name: str = BC_NAME) -> Path:
    manifest = tmp_path / "bc-manifest.yaml"
    manifest.write_text(
        f"product: shopsystem product\n"
        f"bcs:\n"
        f"  - name: {bc_name}\n"
        f"    remote: https://github.com/shopsystem/{bc_name}.git\n"
        f"    role: bc\n"
    )
    return manifest


def _launch(tmp_path: Path) -> FakeDockerDriver:
    driver = FakeDockerDriver()
    controller = BcContainerController(driver)
    home = _make_credential_home(tmp_path)
    manifest = _make_manifest(tmp_path)
    result = controller.launch(
        bc_name=BC_NAME,
        repo_url="https://example.invalid/shopsystem-messaging.git",
        startup_prompt="anything",
        manifest_path=manifest,
        credential_home=home,
    )
    assert result.exit_code == 0, (
        f"launch failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    return driver


def _def_placement_call_index(driver: FakeDockerDriver):
    """Index of the exec_run that PLACES the fabro def bundle into the
    container (the /bin/sh -c base64-decode script targeting
    FABRO_DEF_CONTAINER_DIR)."""
    for i, c in enumerate(driver.exec_calls):
        if (
            c.command[:2] == ["/bin/sh", "-c"]
            and len(c.command) >= 3
            and FABRO_DEF_CONTAINER_DIR in c.command[2]
            and "base64 -d" in c.command[2]
        ):
            return i
    return None


def test_launch_places_the_def_bundle_into_the_container(tmp_path):
    """bc-container launch execs a placement step writing the def bundle to
    /workspace/.fabro/ (criterion 1)."""
    driver = _launch(tmp_path)
    idx = _def_placement_call_index(driver)
    assert idx is not None, (
        "Expected a def-bundle placement exec_run targeting "
        f"{FABRO_DEF_CONTAINER_DIR}.  exec_calls: "
        f"{[c.command[:3] for c in driver.exec_calls]!r}"
    )


def test_placement_script_carries_all_fifteen_files_byte_verbatim(tmp_path):
    """The placement script base64-decodes each of the 15 def files into its
    def-root-relative path under /workspace/.fabro/, byte-identical to the
    shipped asset."""
    driver = _launch(tmp_path)
    idx = _def_placement_call_index(driver)
    assert idx is not None
    script = driver.exec_calls[idx].command[2]

    assets = _load_fabro_def_files()
    for rel in EXPECTED_DEF_FILES:
        target = f"{FABRO_DEF_CONTAINER_DIR}/{rel}"
        assert target in script, f"placement script does not target {target}"
        b64 = base64.b64encode(assets[rel]).decode("ascii")
        assert b64 in script, (
            f"placement script does not carry byte-verbatim base64 for {rel}"
        )


def test_placement_writes_nothing_but_placeholder_into_the_container_vault(tmp_path):
    """The vault bytes placed into the container are the __PLACEHOLDER__-only
    asset — no real secret is introduced by placement (ADR-049)."""
    driver = _launch(tmp_path)
    idx = _def_placement_call_index(driver)
    assert idx is not None
    script = driver.exec_calls[idx].command[2]

    vault_bytes = _load_fabro_def_files()["vaults/default/secrets.json"]
    doc = json.loads(vault_bytes.decode())
    assert all(e.get("value") == "__PLACEHOLDER__" for e in doc.values())
    # The exact placeholder-only vault bytes appear in the placement payload.
    assert base64.b64encode(vault_bytes).decode("ascii") in script


def test_placement_runs_as_vscode_before_the_final_ownership_chown(tmp_path):
    """The placement runs as the vscode agent user and BEFORE the final
    /workspace ownership assertion, so the placed .fabro/ tree is handed to
    the agent (ordering + ownership check)."""
    driver = _launch(tmp_path)
    idx = _def_placement_call_index(driver)
    assert idx is not None
    assert driver.exec_calls[idx].user == AGENT_CONTAINER_USER

    # A `chown -R vscode:vscode /workspace` runs AFTER the placement and
    # BEFORE the tmux new-session, covering the freshly-placed .fabro/ tree.
    def is_ws_chown(c) -> bool:
        cmd = c.command
        return (
            bool(cmd)
            and cmd[0] == "chown"
            and "-R" in cmd
            and f"{AGENT_CONTAINER_USER}:{AGENT_CONTAINER_USER}" in cmd
            and any(a.rstrip("/") == CONTAINER_WORKSPACE for a in cmd)
        )

    tmux_idx = next(
        (
            i for i, c in enumerate(driver.exec_calls)
            if c.command[:3] == ["tmux", "new-session", "-d"]
        ),
        None,
    )
    assert tmux_idx is not None
    chown_after = next(
        (
            i for i, c in enumerate(driver.exec_calls)
            if is_ws_chown(c) and i > idx and i < tmux_idx
        ),
        None,
    )
    assert chown_after is not None, (
        "Expected a `chown -R vscode:vscode /workspace` AFTER the def "
        "placement and BEFORE tmux new-session so the placed .fabro/ tree "
        "is agent-owned."
    )


def test_placement_is_additive_tmux_launch_default_unchanged(tmp_path):
    """ADDITIVE (criterion 4): the def-bundle placement does not disturb the
    tmux launch default — a tmux new-session still starts the agent, and the
    placement is a pure add (present in the exec sequence, retiring nothing)."""
    driver = _launch(tmp_path)
    # The tmux agent session still starts (launch default unchanged).
    assert any(
        c.command[:3] == ["tmux", "new-session", "-d"] for c in driver.exec_calls
    ), "tmux new-session (the launch default) must still run"
    # The placement step is present (purely additive).
    assert _def_placement_call_index(driver) is not None


# ---------------------------------------------------------------------------
# Script builder unit checks
# ---------------------------------------------------------------------------

def test_install_script_targets_all_files_and_makes_parent_dirs():
    files = _load_fabro_def_files()
    script = _fabro_def_install_script(files)
    assert f"mkdir -p {FABRO_DEF_CONTAINER_DIR}" in script
    for rel in EXPECTED_DEF_FILES:
        assert f"{FABRO_DEF_CONTAINER_DIR}/{rel}" in script
    # nested subtrees (nodes/, vaults/default/) get their parent dir made
    assert f"{FABRO_DEF_CONTAINER_DIR}/nodes" in script
    assert f"{FABRO_DEF_CONTAINER_DIR}/vaults/default" in script
