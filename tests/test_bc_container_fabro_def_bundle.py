"""
Unit tests pinning the self-contained fabro loop def-bundle delivery
(lead-h2bj — S2 def-bundle delivery, ADR-051).

Background
----------
shopsystem-bc-launcher keeps the bc-shop Implementer->Reviewer loop fabro
def (ADR-051) as SOURCE-mirror asset files under
``src/bc_launcher/assets/fabro-def/``.  Since lead-ona9 (scenario
7700eea079ffe1d8) the def is DELIVERED into every launched container at
``/workspace/.fabro/`` by the shop-templates POUR — exactly as the pour emits
``.claude/skills/`` — and is NO LONGER streamed from a baked wheel asset off
the docker exec STDIN.  The container still carries a self-contained fabro def
runnable FROM THE DEF ALONE, now sourced from the pour.

These tests guard the SOURCE mirror (files present, verbatim bytes, vault
placeholder-only) and the pour-delivery wiring so a future refactor cannot
silently drop a def file, mangle its bytes, re-bake the def, or leak a real
credential into the fabro vault.  They are plain unit tests (NO
``@scenario_hash`` tag): the block-only scenario pin is a SEPARATE companion
dispatch (lead-ky63, @scenario_hash:2dfefe2ba81e418d) and is NOT authored
here.

Invariants pinned
-----------------
* All 15 def-root files are present as launcher SOURCE-mirror assets,
  verbatim-shaped (acceptance criterion 0).
* ``bc-container launch`` DELIVERS ``/workspace/.fabro/`` via the shop-templates
  pour, not a baked-asset stream (lead-ona9 — reworked criterion 1).
* The def's native fabro vault (``vaults/default/secrets.json``) holds ONLY
  ``__PLACEHOLDER__`` for every slot — no real credential (ADR-049,
  acceptance criterion 3).
* Every ``prompt_file=`` node reference in ``workflow.fabro`` resolves to a
  present node asset (structural "runnable-from-the-def-alone" check).
* The pour delivery is ADDITIVE: the tmux launch default and the prior
  provisioning steps are unchanged (acceptance criterion 4).

The launch tests use FakeDockerDriver and require no live Docker.
"""
from __future__ import annotations

import base64
import json
import re
import tomllib
from pathlib import Path

import io
import tarfile

from bc_launcher.controller import (
    AGENT_CONTAINER_USER,
    BcContainerController,
    CONTAINER_WORKSPACE,
    FABRO_DEF_CONTAINER_DIR,
    FABRO_DEF_FILES,
    _fabro_def_asset_root,
    _fabro_def_bundle_tar_b64,
    _fabro_def_install_script,
    _load_fabro_def_files,
)
from tests.fake_driver import FakeDockerDriver


def _unpack_streamed_bundle(tar_b64: str) -> dict[str, bytes]:
    """Decode the base64 tar the placement streams on STDIN into a
    def-root-relative path -> bytes map, mirroring the in-container
    `base64 -d | tar -x`."""
    raw = base64.b64decode(tar_b64)
    out: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r") as tar:
        for member in tar.getmembers():
            if member.isfile():
                out[member.name] = tar.extractfile(member).read()
    return out


BC_NAME = "shopsystem-messaging"

# The 18 def-root-relative paths the bundle MUST ship (acceptance criterion
# 0).  Enumerated independently of the source constant so a change to
# FABRO_DEF_FILES that drops or renames a file fails this test loudly.
# lead-odd9 / ADR-058 D2 added the reactive dispatcher def (dispatcher.fabro +
# dispatcher.toml) alongside the UNCHANGED ADR-051 workflow.fabro child def;
# lead-3zzu / ADR-058 Amendment 2 added dispatch_acp_agent.py, the NON-LLM ACP
# script-agent the dispatcher's backend="acp" `dispatch` node drives.
EXPECTED_DEF_FILES = (
    "dispatcher.fabro",
    "dispatcher.toml",
    "dispatch_acp_agent.py",
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

    # Exactly the enumerated files under the asset root — no thinner, no extra.
    present = {
        str(p.relative_to(root)).replace("\\", "/")
        for p in root.rglob("*")
        if p.is_file()
    }
    assert present == set(EXPECTED_DEF_FILES), (
        "def-bundle asset set does not match the expected files.\n"
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


def test_launch_delivers_the_def_via_the_shop_templates_pour(tmp_path):
    """lead-ona9 (reworked criterion 1): bc-container launch DELIVERS
    /workspace/.fabro/ via the shop-templates pour — the pour runs
    `shop-templates update --target /workspace` inside the workspace and emits
    the fabro def, exactly as it emits .claude/skills/."""
    driver = _launch(tmp_path)
    container = f"bc-{BC_NAME}"

    pour = [
        c for c in driver.exec_calls
        if c.container == container
        and c.command[:2] == ["shop-templates", "update"]
        and "--target" in c.command
        and c.command[c.command.index("--target") + 1] == "/workspace"
    ]
    assert pour, (
        "Expected a `shop-templates update --target /workspace` pour to deliver "
        f"the fabro def. exec_calls: {[c.command[:3] for c in driver.exec_calls]!r}"
    )
    assert driver.workspace_fabro(container), (
        "the shop-templates pour must emit /workspace/.fabro/ at launch"
    )


def test_clone_launch_does_not_stream_the_def_off_a_baked_asset(tmp_path):
    """lead-ona9 retirement teeth: the CLONE path no longer streams the def
    bundle from a baked asset (base64 tar over docker exec STDIN targeting
    /workspace/.fabro/).  Delivery is the pour, so NO such placement exec runs.

    TEETH: reintroduce `_place_fabro_def_bundle` on the clone path -> a base64
    tar def-placement exec targeting FABRO_DEF_CONTAINER_DIR reappears -> RED
    (the def is baked/streamed again, contradicting the pour delivery)."""
    driver = _launch(tmp_path)
    assert _def_placement_call_index(driver) is None, (
        "the clone path must NOT stream the def bundle from a baked asset; "
        "the def is delivered by the shop-templates pour. Offending exec: "
        f"{driver.exec_calls[_def_placement_call_index(driver)].command[:3]!r}"
    )


def test_pour_delivery_is_additive_tmux_launch_default_unchanged(tmp_path):
    """ADDITIVE (criterion 4): the pour delivery of /workspace/.fabro/ does not
    disturb the tmux launch default — a tmux new-session still starts the
    agent, and the fabro def is still delivered (now by the pour)."""
    driver = _launch(tmp_path)
    container = f"bc-{BC_NAME}"
    # The tmux agent session still starts (launch default unchanged).
    assert any(
        c.command[:3] == ["tmux", "new-session", "-d"] for c in driver.exec_calls
    ), "tmux new-session (the launch default) must still run"
    # The fabro def is still delivered (purely additive), now via the pour.
    assert driver.workspace_fabro(container), (
        "the pour must still deliver /workspace/.fabro/ (additive)"
    )


# ---------------------------------------------------------------------------
# Script builder unit checks
# ---------------------------------------------------------------------------

def test_install_script_is_fixed_size_and_carries_no_file_content():
    """lead-m4zt: the install script is a FIXED, tiny constant that unpacks the
    STDIN-streamed tar into the dest dir — it carries NO file content, so its
    length does not grow with the bundle and never approaches MAX_ARG_STRLEN."""
    files = _load_fabro_def_files()
    script = _fabro_def_install_script()
    assert f"mkdir -p {FABRO_DEF_CONTAINER_DIR}" in script
    assert "base64 -d" in script and "tar -x" in script
    assert FABRO_DEF_CONTAINER_DIR in script
    # No file content is inlined: the script is far smaller than any single
    # file's base64, and smaller than the per-argument kernel limit.
    from bc_launcher.controller import MAX_ARG_STRLEN
    assert len(script.encode()) < 512
    assert len(script.encode()) < MAX_ARG_STRLEN
    for rel in EXPECTED_DEF_FILES:
        b64 = base64.b64encode(files[rel]).decode("ascii")
        assert b64 not in script, (
            f"install script must NOT inline file content for {rel} (E2BIG)"
        )


def test_streamed_tar_reproduces_every_file_and_subtree_byte_verbatim():
    """The STDIN-streamed base64 tar packs every def file at its def-root path
    (reproducing the nodes/ and vaults/default/ subtrees) byte-verbatim."""
    files = _load_fabro_def_files()
    placed = _unpack_streamed_bundle(_fabro_def_bundle_tar_b64(files))
    assert set(placed) == set(EXPECTED_DEF_FILES)
    for rel in EXPECTED_DEF_FILES:
        assert placed[rel] == files[rel]
    # the nested subtrees are represented by their member paths
    assert any(name.startswith("nodes/") for name in placed)
    assert any(name.startswith("vaults/default/") for name in placed)
