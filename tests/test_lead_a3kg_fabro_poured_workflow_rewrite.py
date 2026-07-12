"""lead-a3kg — the N4 fabro-orchestrator path rewrites the POURED
`/workspace/.fabro/workflow.toml` IN THE CONTAINER, not the retired baked host
asset (uyj1 completion; folds lead-bq2z).

Background
----------
Under N4 (lead-ona9) the self-contained fabro loop def is delivered into every
launched container by the shop-templates POUR — the pour emits
`/workspace/.fabro/` exactly as it emits `.claude/skills/`, and the baked
`src/bc_launcher/assets/fabro-def/` bundle is retired from the wheel
(pyproject package-data) and the bc-base image.  The def source mirror remains
in the repo ONLY as the def source.

The BUG this dispatch closes: the fabro-orchestrator wiring's workflow.toml
BC_NAME/WORK_ID rewrite (`settings._fabro_workflow_toml_install_script`) still
read the retired baked host asset (`_fabro_def_asset_root()/workflow.toml`).
In a source checkout the mirror is present so it read green; in a real
installed wheel/image, once the package-data removal ships, that path
FileNotFoundErrors.  The rewrite must instead READ the poured
`/workspace/.fabro/workflow.toml` IN the container, rewrite BC_NAME/WORK_ID on
the host, and WRITE it back — never the host asset.

FIDELITY (test-fidelity-for-image-layer-container-runtime-scenarios): every
assertion binds to the REAL launcher's recorded exec_calls over the
FakeDockerDriver — the actual `exec_calls` the controller issues on the fabro
launch path — never a model of the launcher.  Docker is unavailable in this BC
env; these tests inspect the launcher's ISSUED commands structurally.

TEETH: with the wiring reading the retired host asset (the pre-fix code), NO
in-container read of the poured workflow.toml is issued and the write-back
carries the HOST-asset bytes — so the poured-content sentinel is absent from
the write-back and the read-exec locator returns None -> RED.
"""
from __future__ import annotations

import base64
import re
from pathlib import Path

from bc_launcher.controller import (
    BcContainerController,
    FABRO_WORKFLOW_TOML_CONTAINER_PATH,
    FABRO_WORKFLOW_TOML_DEFAULT_BC_NAME,
    FABRO_WORKFLOW_TOML_DEFAULT_WORK_ID,
    FABRO_DEF_CONTAINER_DIR,
)
from tests.fake_driver import FakeDockerDriver

BC_NAME = "shopsystem-messaging"
WORK_ID = "lead-a3kg-work-77"
# A distinctive poured workflow.toml: the bundle-default identity (so the
# rewrite has something to replace) PLUS a unique sentinel comment that the
# HOST asset does NOT carry.  A write-back derived from the CONTAINER file
# preserves the sentinel; one derived from the host asset does not.
POURED_SENTINEL = "# POURED-CONTAINER-SENTINEL-a3kg-8e3d1f"
POURED_WORKFLOW_TOML = (
    f"{POURED_SENTINEL}\n"
    "[run.inputs]\n"
    f'BC_NAME = "{FABRO_WORKFLOW_TOML_DEFAULT_BC_NAME}"\n'
    f'WORK_ID = "{FABRO_WORKFLOW_TOML_DEFAULT_WORK_ID}"\n'
    "\n"
    "[run.environment.env]\n"
    f'BC_NAME = "{FABRO_WORKFLOW_TOML_DEFAULT_BC_NAME}"\n'
    f'WORK_ID = "{FABRO_WORKFLOW_TOML_DEFAULT_WORK_ID}"\n'
)


def _make_credential_home(tmp_path: Path) -> Path:
    home = tmp_path / "fake_home"
    home.mkdir()
    (home / ".claude").mkdir()
    (home / ".claude" / ".claude.json").write_text("{}")
    (home / ".config" / "gh").mkdir(parents=True)
    (home / ".gitconfig").write_text("")
    return home


def _make_manifest(tmp_path: Path) -> Path:
    manifest = tmp_path / "bc-manifest.yaml"
    manifest.write_text(
        "product: shopsystem product\n"
        "bcs:\n"
        f"  - name: {BC_NAME}\n"
        f"    remote: https://github.com/shopsystem/{BC_NAME}.git\n"
        "    role: bc\n"
    )
    return manifest


def _launch_clone_fabro(tmp_path: Path) -> FakeDockerDriver:
    """Drive the REAL launcher on the CLONE + fabro path (the pour delivers
    /workspace/.fabro/), seeding a distinctive poured workflow.toml."""
    driver = FakeDockerDriver()
    controller = BcContainerController(driver)
    container = f"bc-{BC_NAME}"
    driver.set_poured_workflow_toml(container, POURED_WORKFLOW_TOML)
    result = controller.launch(
        bc_name=BC_NAME,
        repo_url="https://example.invalid/shopsystem-messaging.git",
        launch_path="fabro",
        work_id=WORK_ID,
        startup_prompt="anything",
        manifest_path=_make_manifest(tmp_path),
        credential_home=_make_credential_home(tmp_path),
    )
    assert result.exit_code == 0, (
        f"clone fabro launch failed: stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    return driver


def _container() -> str:
    return f"bc-{BC_NAME}"


def _poured_read_call(driver: FakeDockerDriver):
    """The in-container READ of the poured workflow.toml — a `/bin/sh -c`
    exec that `base64`-encodes the poured path to stdout (NOT a `base64 -d`
    write-back)."""
    for c in driver.exec_calls:
        if (
            c.command[:2] == ["/bin/sh", "-c"]
            and len(c.command) >= 3
            and FABRO_WORKFLOW_TOML_CONTAINER_PATH in c.command[2]
            and "base64" in c.command[2]
            and "base64 -d" not in c.command[2]
        ):
            return c
    return None


def _workflow_toml_writeback_call(driver: FakeDockerDriver):
    """The write-back of the rewritten workflow.toml — a `/bin/sh -c` exec that
    `base64 -d`-writes over the poured path (excluding the def-bundle
    placement, which also writes workflow.fabro)."""
    for c in driver.exec_calls:
        if (
            c.command[:2] == ["/bin/sh", "-c"]
            and len(c.command) >= 3
            and FABRO_WORKFLOW_TOML_CONTAINER_PATH in c.command[2]
            and "base64 -d" in c.command[2]
            and f"{FABRO_DEF_CONTAINER_DIR}/workflow.fabro" not in c.command[2]
        ):
            return c
    return None


def _recover_written_bytes(script: str) -> str:
    m = re.search(r"printf %s '?([A-Za-z0-9+/=]+)'? \| base64 -d", script)
    assert m, f"could not recover base64 payload from script: {script!r}"
    return base64.b64decode(m.group(1)).decode("utf-8")


def test_fabro_wiring_reads_the_poured_workflow_toml_in_container(tmp_path):
    """The wiring issues an in-container READ of the poured
    /workspace/.fabro/workflow.toml (base64-to-stdout), so the rewrite operates
    on the def actually present in the container — not the retired baked host
    asset.

    TEETH: the pre-fix wiring read the host asset and issued NO in-container
    read of the poured file -> _poured_read_call returns None -> RED.
    """
    driver = _launch_clone_fabro(tmp_path)
    read_call = _poured_read_call(driver)
    assert read_call is not None, (
        "the fabro wiring must READ the poured "
        f"{FABRO_WORKFLOW_TOML_CONTAINER_PATH} in-container before rewriting it; "
        "no such read exec was issued (it still reads the retired baked host "
        "asset)"
    )


def test_fabro_wiring_rewrite_derives_from_the_poured_container_file(tmp_path):
    """The write-back is derived from the POURED CONTAINER file: it carries the
    poured-content sentinel (proving the source was the container file, not the
    host asset), the launch's ACTUAL BC_NAME/WORK_ID in both tables, and none of
    the bundle-default identity.

    TEETH: reading the host asset produces a write-back WITHOUT the poured
    sentinel (the asset has no sentinel) -> RED.
    """
    driver = _launch_clone_fabro(tmp_path)
    call = _workflow_toml_writeback_call(driver)
    assert call is not None, (
        "the fabro wiring must write the rewritten workflow.toml back over the "
        f"poured {FABRO_WORKFLOW_TOML_CONTAINER_PATH}"
    )
    written = _recover_written_bytes(call.command[2])

    # Proof the rewrite source was the CONTAINER file: the poured sentinel
    # survives into the write-back.  The host asset carries no such sentinel.
    assert POURED_SENTINEL in written, (
        "the write-back must be derived from the POURED container workflow.toml "
        "(its sentinel must survive); the sentinel is absent, so the rewrite "
        f"read the host asset instead. written:\n{written}"
    )
    # The bundle-default identity is rewritten away.
    assert FABRO_WORKFLOW_TOML_DEFAULT_BC_NAME not in written, written
    assert FABRO_WORKFLOW_TOML_DEFAULT_WORK_ID not in written, written
    # The ACTUAL identity is present in BOTH tables ([run.inputs] +
    # [run.environment.env]).
    assert written.count(f'BC_NAME = "{BC_NAME}"') >= 2, written
    assert written.count(f'WORK_ID = "{WORK_ID}"') >= 2, written


def test_workflow_toml_read_and_writeback_builders_exist():
    """The settings module exposes the in-container read + write-back script
    builders the N4 wiring composes, and neither reads the host asset.

    TEETH: the pre-fix module exposes only the asset-reading
    `_fabro_workflow_toml_install_script` and no read/write-back split ->
    ImportError -> RED.
    """
    from bc_launcher.fabro.settings import (
        _fabro_workflow_toml_read_script,
        _fabro_workflow_toml_writeback_script,
    )

    read_script = _fabro_workflow_toml_read_script()
    assert FABRO_WORKFLOW_TOML_CONTAINER_PATH in read_script
    assert "base64" in read_script
    assert "base64 -d" not in read_script

    writeback = _fabro_workflow_toml_writeback_script("hello = 1\n")
    assert FABRO_WORKFLOW_TOML_CONTAINER_PATH in writeback
    assert "base64 -d" in writeback
