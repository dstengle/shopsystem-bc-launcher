"""Step definitions for the fabro-def pour-delivery + baked-bundle-retirement
scenario (lead-ona9, @scenario_hash:7700eea079ffe1d8).

The self-contained fabro loop def is delivered EXACTLY as the ".claude/skills/"
skill-group is:

  * the shop-templates pour run inside the container workspace after clone
    emits "/workspace/.fabro/" (parallel to the ".claude/skills/" pour,
    75ae95be0ecf1640);
  * a "--workspace-mount" launch SKIPS the pour and presents the committed
    "/workspace/.fabro/" byte-unchanged (no launcher write touches it), exactly
    as the committed ".claude/skills/" tree is treated;
  * the fabro-def is therefore no longer a BAKED delivery surface — it is
    pruned from the packaged wheel (pyproject package-data) and the bc-base
    image (docker/bc-base/Dockerfile) — while "src/bc_launcher/assets/fabro-def/"
    remains in the repo as the def SOURCE mirror.

Every launch assertion binds to the REAL launcher's recorded exec_calls over
the FakeDockerDriver — never a model of the launcher.  The unbake assertions
read the ACTUAL pyproject.toml / Dockerfile / asset tree on disk.

The Given/When steps are reused from tests/steps/container.py.
"""
from __future__ import annotations

from pathlib import Path

from pytest_bdd import then

from bc_launcher.controller import BcContainerController, FABRO_DEF_CONTAINER_DIR
from tests.fake_driver import FakeDockerDriver

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ASSET_MIRROR = _REPO_ROOT / "src" / "bc_launcher" / "assets" / "fabro-def"


# ---------------------------------------------------------------------------
# Behavior 1 — the pour emits /workspace/.fabro/ after clone
# ---------------------------------------------------------------------------

@then(
    "the shop-templates pour has been run inside the container's workspace "
    'directory and has emitted "/workspace/.fabro/" after clone, parallel to '
    'the ".claude/skills/" pour (scenario @scenario_hash:75ae95be0ecf1640)'
)
def then_pour_emits_fabro(ctx, fake_driver):
    container = ctx["container_name"]

    # The pour ran the VALID `shop-templates update --target /workspace` inside
    # the container workspace (same invocation that emits ".claude/skills/").
    pour = [
        c for c in fake_driver.exec_calls
        if c.container == container
        and c.command[:2] == ["shop-templates", "update"]
        and "--target" in c.command
        and c.command[c.command.index("--target") + 1] == "/workspace"
    ]
    assert pour, (
        "Expected a `shop-templates update --target /workspace` pour exec "
        "during launch; none ran. The pour is the delivery mechanism for "
        f"/workspace/.fabro/. shop-templates execs: "
        f"{[c.command for c in fake_driver.exec_calls if c.command[:1] == ['shop-templates']]}"
    )

    # The pour EMITTED /workspace/.fabro/ (parallel to the .claude/skills/ pour).
    assert fake_driver.workspace_fabro(container), (
        "The workspace's /workspace/.fabro/ directory was NOT emitted by the "
        "shop-templates pour after launch completed. The fabro loop def must "
        "be delivered by the pour, exactly as the .claude/skills/ skill-group "
        "is."
    )
    # And the parallel .claude/skills/ pour still populates, confirming the two
    # trees are delivered by the SAME pour step.
    assert fake_driver.workspace_skills(container), (
        "The parallel .claude/skills/ pour did not populate; the fabro pour "
        "must run alongside it, not replace it."
    )


# ---------------------------------------------------------------------------
# Behavior 2 — --workspace-mount SKIPS the pour; committed .fabro/ byte-unchanged
# ---------------------------------------------------------------------------

def _credential_home(tmp_path: Path) -> Path:
    home = tmp_path / "wsmount_home"
    home.mkdir()
    (home / ".claude").mkdir()
    (home / ".claude" / ".claude.json").write_text("{}")
    (home / ".config" / "gh").mkdir(parents=True)
    (home / ".gitconfig").write_text("")
    return home


def _manifest(tmp_path: Path, bc_name: str) -> Path:
    manifest = tmp_path / "wsmount-manifest.yaml"
    manifest.write_text(
        "product: shopsystem product\n"
        "bcs:\n"
        f"  - name: {bc_name}\n"
        f"    remote: https://github.com/shopsystem/{bc_name}.git\n"
        "    role: bc\n"
    )
    return manifest


@then(
    'when bc-container launch is run with "--workspace-mount" the pour is '
    'SKIPPED and the committed "/workspace/.fabro/" is used byte-unchanged, '
    'exactly as the committed ".claude/skills/" tree is treated'
)
def then_workspace_mount_skips_pour(ctx, tmp_path):
    bc_name = ctx.get("bc_name", "shopsystem-messaging")
    host_tree = "/host/live/shopsystem-messaging"

    driver = FakeDockerDriver()
    # The mounted host tree carries a COMMITTED .fabro/ and .claude/skills/,
    # exactly as a poured-then-committed BC repo would.
    driver.set_host_tree_snapshot(
        host_tree,
        beads_registry='{"id":"seed-1","title":"committed"}\n',
        claude_skills="committed-skill-group/bc-router-health\n",
        fabro_def="committed-fabro-def/workflow.fabro\n",
    )
    controller = BcContainerController(driver)
    result = controller.launch(
        bc_name=bc_name,
        repo_url=None,
        workspace_mount=host_tree,
        manifest_path=_manifest(tmp_path, bc_name),
        credential_home=_credential_home(tmp_path),
    )
    assert result.exit_code == 0, (
        f"workspace-mount launch failed: stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    container = f"bc-{bc_name}"

    # The pour is SKIPPED on the workspace-mount path (same guard that skips the
    # .claude/skills/ re-pour).
    assert not driver.shop_templates_update_ran(container), (
        "a --workspace-mount launch must SKIP the shop-templates pour; it ran"
    )
    # No launcher exec writes into /workspace/.fabro/ — the committed tree is
    # presented byte-unchanged, exactly as the committed .claude/skills/ tree.
    fabro_writes = [
        c for c in driver.exec_calls
        if len(c.command) >= 3
        and c.command[:2] == ["/bin/sh", "-c"]
        and FABRO_DEF_CONTAINER_DIR in c.command[2]
        and ("base64 -d" in c.command[2] or "tar -x" in c.command[2])
    ]
    assert not fabro_writes, (
        "a --workspace-mount launch must not write into /workspace/.fabro/ "
        "(the committed def is used byte-unchanged); offending execs: "
        f"{[c.command[:3] for c in fabro_writes]}"
    )
    # And the committed .fabro/ snapshot is unchanged (parallel to the
    # committed .claude/skills/ byte-unchanged invariant).
    assert driver.mounted_tree_byte_unchanged(container, host_tree), (
        "the mounted host tree (its committed .fabro/ and .claude/skills/) "
        "must remain byte-unchanged on a workspace-mount launch"
    )


# ---------------------------------------------------------------------------
# Behavior 3 — the fabro-def is no longer a BAKED delivery surface
# ---------------------------------------------------------------------------

@then(
    'the fabro-def bundle formerly baked from "src/bc_launcher/assets/fabro-def/" '
    "is absent from the packaged wheel (pyproject package-data) and the bc-base "
    "image (docker/bc-base/Dockerfile) — no longer a baked delivery surface "
    '— while the shop-templates pour delivers "/workspace/.fabro/" at '
    "launch, the repo source mirror remaining as the def source"
)
def then_fabro_def_unbaked(ctx, fake_driver):
    # (a) ABSENT from the packaged wheel: the pyproject package-data must NOT
    #     ship the fabro-def bundle.
    pyproject = (_REPO_ROOT / "pyproject.toml").read_text()
    assert "fabro-def" not in pyproject, (
        "pyproject.toml still ships the fabro-def bundle as package-data; it "
        "must be pruned so the wheel no longer bakes the def (it is delivered "
        f"by the shop-templates pour). pyproject:\n{pyproject}"
    )

    # (b) ABSENT from the bc-base image: the Dockerfile must NOT bake the
    #     fabro-def as a delivery surface — no COPY/ADD of the asset tree — and
    #     it must carry an explicit retirement annotation documenting that the
    #     def is delivered by the shop-templates pour instead.
    dockerfile = (_REPO_ROOT / "docker" / "bc-base" / "Dockerfile").read_text()
    copy_bakes = [
        ln for ln in dockerfile.splitlines()
        if "fabro-def" in ln
        and ln.lstrip().upper().startswith(("COPY", "ADD"))
    ]
    assert not copy_bakes, (
        "the bc-base Dockerfile must NOT COPY/ADD the fabro-def bundle as a "
        f"baked delivery surface; offending lines: {copy_bakes}"
    )
    assert "fabro-def" in dockerfile and "shop-templates pour" in dockerfile, (
        "the bc-base Dockerfile must carry an explicit annotation that the "
        "fabro-def is no longer baked and is delivered by the shop-templates "
        "pour"
    )

    # (c) the shop-templates pour is the delivery surface at launch — the
    #     launched container's /workspace/.fabro/ was emitted by the pour.
    container = ctx["container_name"]
    assert fake_driver.workspace_fabro(container), (
        "the shop-templates pour must deliver /workspace/.fabro/ at launch"
    )

    # (d) the repo SOURCE mirror remains: src/bc_launcher/assets/fabro-def/ is
    #     still present as the def source.
    assert (_ASSET_MIRROR / "workflow.fabro").is_file(), (
        "src/bc_launcher/assets/fabro-def/ must remain in the repo as the def "
        "source mirror (KEEP the repo source dir)"
    )
