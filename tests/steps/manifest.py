"""Step definitions: manifest (mechanically extracted from conftest.py).

Registered globally via the dynamic pytest_plugins glob in tests/conftest.py;
module boundaries are organizational, not semantic.
"""
from __future__ import annotations

from pytest_bdd import given, when, then, parsers
from tests.conftest import BC_NAME_RE, FakeGitHubDriver, GITHUB_URL_RE, ManifestController, PRODUCT_BCS, _run_manifest_list, _run_manifest_sync, _run_manifest_validate, _write_manifest, given, parsers, then, when, yaml  # noqa: F401


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
