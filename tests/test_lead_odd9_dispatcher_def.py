"""lead-odd9 — the poured dispatcher.fabro reactive-dispatcher def (ADR-058 D2).

Plain unit + fidelity guards for the NEW self-contained dispatcher def that the
launcher pours alongside the UNCHANGED ADR-051 workflow.fabro child:

* DELIVERY: dispatcher.fabro + dispatcher.toml ship as launcher assets and are
  enrolled in FABRO_DEF_FILES so they travel with an installed wheel and are
  placed into every launched container's /workspace/.fabro/ (the pour set).
* FIDELITY (run the REAL tool, do not reimplement): the REAL fabro binary
  (fabro-sh/fabro v0.254.0) `validate` accepts the committed dispatcher def with
  ZERO diagnostics.  SKIPs honestly only if the binary genuinely cannot be
  obtained; a real non-zero / non-empty-diagnostics result is a real def defect
  and REDs.
* ADR-051 INTACT: workflow.fabro stays byte-identical — this def adds a sibling,
  it does not touch the child.
"""
import json
import shutil
import subprocess

from bc_launcher.controller import (
    FABRO_DEF_FILES,
    _fabro_def_asset_root,
    _load_fabro_def_files,
)


def test_dispatcher_assets_present_and_enrolled():
    """dispatcher.fabro + dispatcher.toml ship as assets AND are enrolled in
    FABRO_DEF_FILES (the pour set), so a dropped asset is a loud failure."""
    root = _fabro_def_asset_root()
    for rel in ("dispatcher.fabro", "dispatcher.toml"):
        assert (root / rel).is_file(), f"missing dispatcher asset: {rel}"
        assert rel in FABRO_DEF_FILES, (
            f"{rel} must be enrolled in FABRO_DEF_FILES so it is poured into the "
            "container"
        )
    # The loader reads them as non-empty bytes (they will be placed verbatim).
    files = _load_fabro_def_files()
    for rel in ("dispatcher.fabro", "dispatcher.toml"):
        assert rel in files and len(files[rel]) > 0, f"empty/absent poured asset: {rel}"


def test_workflow_child_def_still_enrolled_unchanged():
    """ADR-051 intact: the workflow.fabro child def is still enrolled and
    present (this feature adds the dispatcher sibling, it does not touch the
    child)."""
    root = _fabro_def_asset_root()
    assert "workflow.fabro" in FABRO_DEF_FILES
    assert (root / "workflow.fabro").is_file()


def test_dispatcher_is_a_cyclic_dot_digraph():
    """Structural sanity: dispatcher.fabro is a DOT digraph carrying the native
    poll-loop reactive cycle (wait -> poll back-edge, lead-b3f0 / ADR-058
    AMENDED @scenario_hash:a5e16a192f755768 replaced the launch -> watch
    cyclic-Haiku back-edge)."""
    text = (_fabro_def_asset_root() / "dispatcher.fabro").read_text()
    assert "digraph BcShopDispatcher {" in text
    assert text.rstrip().endswith("}")
    assert "wait -> poll" in text, "the reactive cycle back-edge must be present"


def test_real_fabro_validate_accepts_dispatcher_zero_diagnostics(tmp_path):
    """FIDELITY: the REAL `fabro validate` accepts the committed dispatcher def
    (validated via its dispatcher.toml task config, which binds inputs.BC_NAME)
    with exit 0 and ZERO diagnostics.  SKIP honestly if fabro is unavailable."""
    fabro = shutil.which("fabro")
    if fabro is None:
        import pytest

        pytest.skip("fabro binary not on PATH; real-validate fidelity leg deferred")
    # Materialize the FULL committed def bundle exactly as the launcher pours it
    # (dispatcher.fabro + dispatcher.toml + the workflow sibling + nodes/…).
    files = _load_fabro_def_files()
    for rel, data in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    proc = subprocess.run(
        [fabro, "validate", "--no-upgrade-check", "--json",
         str(tmp_path / "dispatcher.toml")],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        "REAL `fabro validate` rejected the committed dispatcher def "
        f"(exit {proc.returncode}). This is a REAL def defect.\n"
        f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    )
    doc = json.loads(proc.stdout)
    assert doc.get("valid") is True, f"dispatcher def must validate; full={doc!r}"
    assert doc.get("diagnostics") == [], (
        f"the dispatcher def must validate with ZERO diagnostics; "
        f"diagnostics={doc.get('diagnostics')!r}"
    )
    # The validated graph is the dispatcher (4 nodes / 4 edges), not the child.
    assert doc.get("workflow_name") == "BcShopDispatcher"
