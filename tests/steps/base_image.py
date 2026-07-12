"""Step definitions: base_image (mechanically extracted from conftest.py).

Registered globally via the dynamic pytest_plugins glob in tests/conftest.py;
module boundaries are organizational, not semantic.
"""
from __future__ import annotations

from pytest_bdd import given, when, then, parsers
from tests.conftest import AGENT_VAULT_PLACEHOLDER_TOKEN, _REPO_ROOT, given, parsers, re, subprocess, sys, then, when  # noqa: F401
from tests.support.base_image import _AGENT_VAULT_AUTH_VERSION, _AGENT_VAULT_CONTAINER_CA_PATH, _AGENT_VAULT_TRUST_VARS, _ANTHROPIC_OAUTH_SHIM_NAME, _BAKED_DEP_CANONICAL_REPOS, _BC_BASE_BEADS_BINARY_OWNER, _BC_BASE_BEADS_BINARY_VERSION, _BC_BASE_DOCKERFILE_REL, _BC_BASE_FRAMEWORK_CLI_PINS, _BOOTSTRAP_FRAMEWORK_CLIS, _FABRO_CANONICAL_REPO, _HEALTHCHECK_BROKER_ENV, _HEALTHCHECK_DB_ENV, _SELF_PIN_CANONICAL_REPO, _SELF_PIN_DEP_KEY, _UPSTREAM_BASE_VERSION_LABEL, _baked_claude_json, _baked_shop_templates_version, _bc_base_dockerfile_text, _bootstrap_entrypoint_path, _bss3_poll_exec_body, _build_step_for_image, _centralized_poll_workflow, _committed_oauth_shim_path, _dockerfile_arg_declared, _dockerfile_env_value, _dockerfile_healthcheck_directive, _effective_final_user, _find_step, _healthcheck_script_path, _load_workflows, _parse_kv_block, _publish_workflow_doc, _shop_templates_pinned_by_version_shape, _strip_sh_comments, _strip_yaml_comments, _top_level_imported_modules, _workflow_on, _workflow_text  # noqa: F401
from tests.support.common import _REAL_OAUTH_TOKEN, _baked_credentials_json, _ca_trust_script_path, _find_bc_base_dockerfile, _strip_dockerfile_comments  # noqa: F401


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


@when("the bc-base CA-trust script content is inspected")
def when_ca_trust_script_inspected(ctx):
    ctx["bc_base_dockerfile"] = _find_bc_base_dockerfile()
    ctx["ca_trust_script"] = _ca_trust_script_path()


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
    'pin is "v0.52.1", the executable body treats the behind-or-equal result '
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
    'an executable body that rewrote the "shop-templates" pin from "v0.52.1" '
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
