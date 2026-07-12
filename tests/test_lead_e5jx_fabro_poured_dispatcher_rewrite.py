"""lead-e5jx — the fabro-orchestrator wiring rewrites the POURED
`/workspace/.fabro/dispatcher.toml` BC_NAME/WORK_ID to the launch's ACTUAL
identity, not only workflow.toml (LAST fabro-engage layer).

Background
----------
The reactive-persistent engage runs `fabro run dispatcher.toml` (ADR-058).  The
dispatcher's native watch/dispatch nodes read $BC_NAME from dispatcher.toml's
[run.environment.env] overlay (which `fabro run -I` does NOT override for the
native script= sandbox).  The poured dispatcher.toml ships the bundle-default
identity `BC_NAME = "fabro-throwaway"` in BOTH [run.inputs] and
[run.environment.env] — so without a rewrite the reactive watcher runs
`dispatch_acp_agent.py --bc fabro-throwaway` / `shop-msg watch --bc
fabro-throwaway` (the bundle default) instead of the launch BC.

The BUG this dispatch closes: `_place_fabro_def_and_wiring` rewrote ONLY the
poured workflow.toml's BC_NAME/WORK_ID, never the poured dispatcher.toml.  The
fix parameterizes the rewrite by `toml_path` and calls it for BOTH
`/workspace/.fabro/workflow.toml` AND `/workspace/.fabro/dispatcher.toml`.

FIDELITY (test-fidelity-for-image-layer-container-runtime-scenarios): every
assertion binds to the REAL launcher's recorded exec_calls over the
FakeDockerDriver — the actual `exec_calls` the controller issues on the fabro
launch path — never a model of the launcher.  Docker is unavailable in this BC
env; these tests inspect the launcher's ISSUED commands structurally.

TEETH: with the wiring rewriting ONLY workflow.toml (the pre-fix code), NO
in-container read of the poured dispatcher.toml is issued and no write-back
carries the launch BC_NAME — so the dispatcher read-locator returns None and
the write-back locator returns None -> RED.
"""
from __future__ import annotations

import base64
import re
from pathlib import Path

from bc_launcher.controller import (
    BcContainerController,
    FABRO_WORKFLOW_TOML_DEFAULT_BC_NAME,
    FABRO_WORKFLOW_TOML_DEFAULT_WORK_ID,
    FABRO_DEF_CONTAINER_DIR,
)
from tests.fake_driver import (
    FakeDockerDriver,
    FABRO_DISPATCHER_TOML_CONTAINER_PATH,
)

BC_NAME = "shopsystem-knowledge"
WORK_ID = "lead-e5jx-work-42"
# A distinctive poured dispatcher.toml: the bundle-default identity (so the
# rewrite has something to replace) PLUS a unique sentinel comment that proves
# the write-back was derived from the CONTAINER file.  dispatcher.toml carries
# ONLY BC_NAME (no WORK_ID — the dispatcher discovers work_ids at runtime), in
# BOTH [run.inputs] and [run.environment.env].
POURED_SENTINEL = "# POURED-DISPATCHER-SENTINEL-e5jx-7c4a2e"
POURED_DISPATCHER_TOML = (
    f"{POURED_SENTINEL}\n"
    "[workflow]\n"
    'graph = "dispatcher.fabro"\n'
    "\n"
    "[run.inputs]\n"
    f'BC_NAME = "{FABRO_WORKFLOW_TOML_DEFAULT_BC_NAME}"\n'
    "\n"
    "[run.environment.env]\n"
    f'BC_NAME = "{FABRO_WORKFLOW_TOML_DEFAULT_BC_NAME}"\n'
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
    /workspace/.fabro/), seeding a distinctive poured dispatcher.toml."""
    driver = FakeDockerDriver()
    controller = BcContainerController(driver)
    container = f"bc-{BC_NAME}"
    driver.set_poured_dispatcher_toml(container, POURED_DISPATCHER_TOML)
    result = controller.launch(
        bc_name=BC_NAME,
        repo_url="https://example.invalid/shopsystem-knowledge.git",
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


def _dispatcher_read_call(driver: FakeDockerDriver):
    """The in-container READ of the poured dispatcher.toml — a `/bin/sh -c`
    exec that `base64`-encodes the poured path to stdout (NOT a `base64 -d`
    write-back)."""
    for c in driver.exec_calls:
        if (
            c.command[:2] == ["/bin/sh", "-c"]
            and len(c.command) >= 3
            and FABRO_DISPATCHER_TOML_CONTAINER_PATH in c.command[2]
            and "base64" in c.command[2]
            and "base64 -d" not in c.command[2]
        ):
            return c
    return None


def _dispatcher_writeback_call(driver: FakeDockerDriver):
    """The write-back of the rewritten dispatcher.toml — a `/bin/sh -c` exec
    that `base64 -d`-writes over the poured path (excluding the def-bundle
    placement, which also writes dispatcher.fabro)."""
    for c in driver.exec_calls:
        if (
            c.command[:2] == ["/bin/sh", "-c"]
            and len(c.command) >= 3
            and FABRO_DISPATCHER_TOML_CONTAINER_PATH in c.command[2]
            and "base64 -d" in c.command[2]
            and f"{FABRO_DEF_CONTAINER_DIR}/dispatcher.fabro" not in c.command[2]
        ):
            return c
    return None


def _recover_written_bytes(script: str) -> str:
    m = re.search(r"printf %s '?([A-Za-z0-9+/=]+)'? \| base64 -d", script)
    assert m, f"could not recover base64 payload from script: {script!r}"
    return base64.b64decode(m.group(1)).decode("utf-8")


def test_fabro_wiring_reads_the_poured_dispatcher_toml_in_container(tmp_path):
    """The wiring issues an in-container READ of the poured
    /workspace/.fabro/dispatcher.toml (base64-to-stdout), so its BC_NAME/WORK_ID
    rewrite operates on the reactive-engage entrypoint actually present in the
    container.

    TEETH: the pre-fix wiring rewrote ONLY workflow.toml and issued NO
    in-container read of the poured dispatcher.toml -> _dispatcher_read_call
    returns None -> RED.
    """
    driver = _launch_clone_fabro(tmp_path)
    read_call = _dispatcher_read_call(driver)
    assert read_call is not None, (
        "the fabro wiring must READ the poured "
        f"{FABRO_DISPATCHER_TOML_CONTAINER_PATH} in-container before rewriting "
        "its BC_NAME/WORK_ID; no such read exec was issued (it rewrites only "
        "workflow.toml, leaving dispatcher.toml at the bundle default "
        "fabro-throwaway)"
    )


def test_fabro_wiring_rewrites_dispatcher_toml_to_the_launch_bc(tmp_path):
    """The dispatcher.toml write-back is derived from the POURED CONTAINER file
    and carries the launch's ACTUAL BC_NAME in BOTH tables, with none of the
    bundle-default identity — so `fabro run dispatcher.toml` yields
    `dispatch_acp_agent.py --bc <launch-bc>` / `shop-msg watch --bc <launch-bc>`
    rather than `--bc fabro-throwaway`.

    TEETH: the pre-fix wiring never wrote back dispatcher.toml, so the write-back
    locator returns None; even a rewrite of the wrong file would not carry the
    dispatcher sentinel -> RED.
    """
    driver = _launch_clone_fabro(tmp_path)
    call = _dispatcher_writeback_call(driver)
    assert call is not None, (
        "the fabro wiring must write the rewritten dispatcher.toml back over the "
        f"poured {FABRO_DISPATCHER_TOML_CONTAINER_PATH}; the reactive engage "
        "reads $BC_NAME from its [run.environment.env] overlay"
    )
    written = _recover_written_bytes(call.command[2])

    # Proof the rewrite source was the CONTAINER file: the poured sentinel
    # survives into the write-back.
    assert POURED_SENTINEL in written, (
        "the write-back must be derived from the POURED container "
        "dispatcher.toml (its sentinel must survive); the sentinel is absent, "
        f"so the rewrite did not read the poured file. written:\n{written}"
    )
    # The bundle-default identity is rewritten away.
    assert FABRO_WORKFLOW_TOML_DEFAULT_BC_NAME not in written, written
    # The ACTUAL launch BC_NAME is present in BOTH tables ([run.inputs] +
    # [run.environment.env]) so the native watch/dispatch nodes target the
    # launch BC, not fabro-throwaway.
    assert written.count(f'BC_NAME = "{BC_NAME}"') >= 2, written


def test_both_workflow_and_dispatcher_toml_are_rewritten(tmp_path):
    """The wiring rewrites BOTH poured tomls — workflow.toml AND dispatcher.toml
    — in-container, so neither the child workflow def nor the reactive
    dispatcher entrypoint runs against the bundle-default identity.

    TEETH: the pre-fix wiring rewrote only workflow.toml, so the dispatcher
    write-back is absent -> RED.
    """
    from tests.fake_driver import FABRO_WORKFLOW_TOML_CONTAINER_PATH

    driver = _launch_clone_fabro(tmp_path)

    def _writeback_for(path: str):
        for c in driver.exec_calls:
            if (
                c.command[:2] == ["/bin/sh", "-c"]
                and len(c.command) >= 3
                and path in c.command[2]
                and "base64 -d" in c.command[2]
                and f"{FABRO_DEF_CONTAINER_DIR}/workflow.fabro" not in c.command[2]
                and f"{FABRO_DEF_CONTAINER_DIR}/dispatcher.fabro"
                not in c.command[2]
            ):
                return c
        return None

    assert _writeback_for(FABRO_WORKFLOW_TOML_CONTAINER_PATH) is not None, (
        "workflow.toml rewrite/write-back is missing"
    )
    assert _writeback_for(FABRO_DISPATCHER_TOML_CONTAINER_PATH) is not None, (
        "dispatcher.toml rewrite/write-back is missing — the reactive engage "
        "would run against the bundle default fabro-throwaway"
    )
