"""Step definitions: unmatched (mechanically extracted from conftest.py).

Registered globally via the dynamic pytest_plugins glob in tests/conftest.py;
module boundaries are organizational, not semantic.
"""
from __future__ import annotations

from pytest_bdd import given, when, then, parsers
from tests.conftest import _CADR_LAUNCH_PATH_FABRO, _CADR_LAUNCH_PATH_TMUX, _L3ZZU_DELIVERY, _L3ZZU_IDEMP, _L3ZZU_STEP, _ODD9_BC, _agent_vault_launch, _b3f0_dispatcher_edges, _b3f0_dispatcher_graph_text, _cadr_build_parser, _cadr_claude_engage_send_keys, _cadr_fabro_run_calls, _cadr_fabro_server_calls, _cadr_tmux_agent_send_keys, _cadr_write_manifest, _ky63_def_asset_root, _l3zzu_dispatch_body, _l3zzu_inspect, _l3zzu_load_acp_agent, _odd9_drive_fabro_launch, given, parsers, re, then, when  # noqa: F401


@given(parsers.parse('the host directory "{host_path}" exists'))
def host_directory_exists(host_path, ctx, tmp_path):
    """
    Create a temporary directory to simulate the named host path existing.

    Maps the symbolic path (e.g. "$HOME/.claude") to a real temp directory
    so the controller's Path.exists() checks pass.  Stores the fake home
    root in ctx['credential_home'] so When steps can pass it to launch().
    """
    credential_home = ctx.setdefault("credential_home", tmp_path / "fake_home")
    # Resolve the symbolic path segment after "$HOME/"
    assert host_path.startswith("$HOME/"), (
        f"host_directory_exists step only handles $HOME/... paths, got: {host_path!r}"
    )
    rel = host_path[len("$HOME/"):]
    target = credential_home / rel
    target.mkdir(parents=True, exist_ok=True)


@given(parsers.parse('the host directory "{host_path}" does not exist'))
def host_directory_does_not_exist(host_path, ctx, tmp_path):
    """
    Ensure the named host directory does NOT exist under the fake home.

    Sets up credential_home in ctx without creating the named directory.
    Other directories listed in subsequent Given steps will still be created.
    """
    credential_home = ctx.setdefault("credential_home", tmp_path / "fake_home")
    credential_home.mkdir(parents=True, exist_ok=True)
    # Explicitly do not create the directory; if it already exists remove it.
    assert host_path.startswith("$HOME/"), (
        f"host_directory_does_not_exist step only handles $HOME/... paths, got: {host_path!r}"
    )
    rel = host_path[len("$HOME/"):]
    target = credential_home / rel
    if target.exists():
        import shutil
        shutil.rmtree(str(target))


@given(parsers.parse('the host file "{host_path}" exists'))
def host_file_exists(host_path, ctx, tmp_path):
    """
    Create a temporary file to simulate the named host file existing.

    Handles both top-level files ($HOME/.gitconfig) and nested files
    ($HOME/.claude/.claude.json).
    """
    credential_home = ctx.setdefault("credential_home", tmp_path / "fake_home")
    assert host_path.startswith("$HOME/"), (
        f"host_file_exists step only handles $HOME/... paths, got: {host_path!r}"
    )
    rel = host_path[len("$HOME/"):]
    target = credential_home / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_text("")


@given(parsers.parse('the host file "{host_path}" does not exist'))
def host_file_does_not_exist(host_path, ctx, tmp_path):
    """
    Ensure the named host file does NOT exist under the fake home.

    The parent directory is preserved (or created) so that the .claude directory
    itself can still exist while its .claude.json child is absent.
    """
    credential_home = ctx.setdefault("credential_home", tmp_path / "fake_home")
    assert host_path.startswith("$HOME/"), (
        f"host_file_does_not_exist step only handles $HOME/... paths, got: {host_path!r}"
    )
    rel = host_path[len("$HOME/"):]
    target = credential_home / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()


@when(parsers.parse('I run bc-container launch with BC name "{bc_name}" and no explicit credential path flags'))
def run_launch_no_credential_flags(bc_name, ctx, fake_driver, controller, tmp_path):
    """Launch without any explicit credential path overrides (uses defaults from ctx['credential_home'])."""
    repo_url = ctx.get("repo_url", f"https://github.com/shopsystem/{bc_name}.git")
    manifest_path = ctx.get("launch_manifest_path")
    if manifest_path is None and "launch_no_manifest" not in ctx:
        default_manifest = tmp_path / "bc-manifest.yaml"
        if not default_manifest.exists():
            import yaml as _yaml
            default_manifest.write_text(_yaml.dump({
                "product": "shopsystem product",
                "bcs": [{"name": bc_name, "remote": f"https://github.com/shopsystem/{bc_name}.git", "role": "bc"}]
            }))
        manifest_path = default_manifest
    credential_home = ctx.get("credential_home")
    result = controller.launch(bc_name=bc_name, repo_url=repo_url,
                               manifest_path=manifest_path,
                               credential_home=credential_home)
    ctx["result"] = result
    ctx.setdefault("all_results", []).append(result)
    ctx["container_name"] = f"bc-{bc_name}"
    ctx["bc_name"] = bc_name


@when(parsers.parse('I run bc-container launch with BC name "{bc_name}" and startup prompt "{prompt}"'))
def run_launch_with_startup_prompt(bc_name, prompt, ctx, fake_driver, controller, tmp_path):
    repo_url = ctx.get("repo_url", f"https://github.com/shopsystem/{bc_name}.git")
    manifest_path = ctx.get("launch_manifest_path")
    if manifest_path is None and "launch_no_manifest" not in ctx:
        default_manifest = tmp_path / "bc-manifest.yaml"
        if not default_manifest.exists():
            import yaml as _yaml
            default_manifest.write_text(_yaml.dump({
                "product": "shopsystem product",
                "bcs": [{"name": bc_name, "remote": repo_url, "role": "bc"}]
            }))
        manifest_path = default_manifest
    credential_home = ctx.get("credential_home")
    result = controller.launch(
        bc_name=bc_name,
        repo_url=repo_url,
        startup_prompt=prompt,
        manifest_path=manifest_path,
        credential_home=credential_home,
    )
    ctx["result"] = result
    ctx["container_name"] = f"bc-{bc_name}"
    ctx["bc_name"] = bc_name
    ctx["startup_prompt"] = prompt


@when("the container starts and the tmux session is active")
def container_starts_and_tmux_active(ctx, fake_driver):
    """No-op: handled by launch via exec_run in the fake driver."""


@when(parsers.parse('I run bc-container inject with BC name "{bc_name}" and prompt text "{prompt}"'))
def run_inject(bc_name, prompt, ctx, fake_driver, controller):
    result = controller.inject(bc_name, prompt)
    ctx["result"] = result
    ctx["last_exec_calls"] = fake_driver.exec_calls
    ctx["bc_name"] = bc_name
    ctx["prompt"] = prompt


@then(parsers.parse('the FakeDockerDriver records that the docker run command for "{container_name}" includes a bind mount with source "{source_token}" and target "{target}"'))
def assert_bind_mount_present(container_name, source_token, target, ctx, fake_driver, tmp_path):
    """
    Assert that the docker run command for the container includes a --mount flag
    with the given source (resolved from $HOME/... to the fake_home path) and target.
    """
    run_cmd = fake_driver.run_command_for_container(container_name)
    assert run_cmd, f"FakeDockerDriver recorded no docker run command for {container_name!r}"

    # Resolve source_token: if it starts with $HOME/, map to the fake_home
    credential_home = ctx.get("credential_home", tmp_path / "fake_home")
    if source_token.startswith("$HOME/"):
        rel = source_token[len("$HOME/"):]
        resolved_source = str(credential_home / rel)
    else:
        resolved_source = source_token

    cmd_str = " ".join(run_cmd)
    assert f"source={resolved_source}" in cmd_str and f"target={target}" in cmd_str, (
        f"Expected bind mount source={resolved_source!r} target={target!r} in docker run command.\n"
        f"Recorded run command: {cmd_str!r}"
    )


@then(parsers.parse('the FakeDockerDriver records that the docker run command for "{container_name}" includes a read-only bind mount with source "{source_token}" and target "{target}"'))
def assert_readonly_bind_mount_present(container_name, source_token, target, ctx, fake_driver, tmp_path):
    """
    Assert that the docker run command includes a read-only --mount flag with the given source and target.
    """
    run_cmd = fake_driver.run_command_for_container(container_name)
    assert run_cmd, f"FakeDockerDriver recorded no docker run command for {container_name!r}"

    credential_home = ctx.get("credential_home", tmp_path / "fake_home")
    if source_token.startswith("$HOME/"):
        rel = source_token[len("$HOME/"):]
        resolved_source = str(credential_home / rel)
    else:
        resolved_source = source_token

    cmd_str = " ".join(run_cmd)
    # Check source, target, and readonly are all in the same --mount spec
    # The spec is: type=bind,source=X,target=Y,readonly
    found = False
    i = 0
    tokens = run_cmd
    while i < len(tokens):
        if tokens[i] == "--mount" and i + 1 < len(tokens):
            spec = tokens[i + 1]
            if (f"source={resolved_source}" in spec
                    and f"target={target}" in spec
                    and "readonly" in spec):
                found = True
                break
        i += 1

    assert found, (
        f"Expected read-only bind mount source={resolved_source!r} target={target!r} "
        f"in docker run command.\nRecorded run command: {cmd_str!r}"
    )


@then(parsers.parse('the FakeDockerDriver records that an exec_run was issued against "{container_name}" copying "{src}" to "{dest}"'))
def assert_exec_copy(container_name, src, dest, ctx, fake_driver):
    """Assert that an exec_run was recorded that runs cp <src> <dest> in the container."""
    cp_calls = [
        c for c in fake_driver.exec_calls
        if c.container == container_name
        and len(c.command) == 3
        and c.command[0] == "cp"
        and c.command[1] == src
        and c.command[2] == dest
    ]
    assert cp_calls, (
        f"Expected exec_run('cp {src} {dest}') against {container_name!r}.\n"
        f"Recorded exec calls: {[(c.container, c.command) for c in fake_driver.exec_calls]!r}"
    )


@then("that exec_run is recorded after the docker run for \"bc-shopsystem-messaging\" and before the tmux new-session exec_run")
def assert_cp_between_run_and_tmux(ctx, fake_driver):
    """
    Assert that the cp exec_run calls appear in the operation/exec log after
    docker run and before the tmux new-session exec_run.
    """
    container_name = "bc-shopsystem-messaging"

    # Find the index of the docker run in the operation_log
    run_idx = None
    for i, (op, name) in enumerate(fake_driver.operation_log):
        if op == "run" and name == container_name:
            run_idx = i
            break
    assert run_idx is not None, (
        f"Expected 'run' entry for {container_name!r} in operation_log: {fake_driver.operation_log!r}"
    )

    # Find cp call indices in exec_calls
    cp_indices = [
        i for i, c in enumerate(fake_driver.exec_calls)
        if c.container == container_name and c.command[0:1] == ["cp"]
    ]
    assert cp_indices, (
        f"Expected at least one 'cp' exec_run against {container_name!r}.\n"
        f"Recorded exec calls: {[(c.container, c.command) for c in fake_driver.exec_calls]!r}"
    )

    # Find tmux new-session index in exec_calls
    tmux_idx = None
    for i, c in enumerate(fake_driver.exec_calls):
        if c.container == container_name and c.command[:3] == ["tmux", "new-session", "-d"]:
            tmux_idx = i
            break
    assert tmux_idx is not None, (
        f"Expected 'tmux new-session' exec_run against {container_name!r}.\n"
        f"Recorded exec calls: {[(c.container, c.command) for c in fake_driver.exec_calls]!r}"
    )

    # All cp calls must come before the tmux new-session call
    for cp_idx in cp_indices:
        assert cp_idx < tmux_idx, (
            f"cp exec_run (index {cp_idx}) must appear before tmux new-session (index {tmux_idx}).\n"
            f"exec_calls: {[(c.container, c.command) for c in fake_driver.exec_calls]!r}"
        )


@then(parsers.parse('the FakeDockerDriver records that the docker run command for "{container_name}" includes exactly these three credential bind mounts:'))
def assert_exactly_three_credential_mounts(container_name, ctx, fake_driver, tmp_path, datatable):
    """
    Assert the docker run command includes exactly the three credential bind mounts
    described in the step's data table.  The $HOME/ prefix is resolved to credential_home.

    pytest-bdd passes data tables as a list of lists (one list per row, including header).
    """
    run_cmd = fake_driver.run_command_for_container(container_name)
    assert run_cmd, f"FakeDockerDriver recorded no docker run command for {container_name!r}"

    credential_home = ctx.get("credential_home", tmp_path / "fake_home")

    # datatable is a list of lists; first row is header, remaining rows are data
    rows = datatable[1:]  # skip header row

    for row in rows:
        source_token = row[0].strip()
        target = row[1].strip()
        readonly_str = row[2].strip()
        readonly = readonly_str.lower() == "true"

        if source_token.startswith("$HOME/"):
            rel = source_token[len("$HOME/"):]
            resolved_source = str(credential_home / rel)
        else:
            resolved_source = source_token

        # Check this mount appears in the run command
        found = False
        tokens = run_cmd
        i = 0
        while i < len(tokens):
            if tokens[i] == "--mount" and i + 1 < len(tokens):
                spec = tokens[i + 1]
                source_ok = f"source={resolved_source}" in spec
                target_ok = f"target={target}" in spec
                ro_ok = ("readonly" in spec) == readonly
                if source_ok and target_ok and ro_ok:
                    found = True
                    break
            i += 1

        assert found, (
            f"Expected mount source={resolved_source!r} target={target!r} readonly={readonly} "
            f"in docker run command for {container_name!r}.\n"
            f"Run command: {' '.join(run_cmd)!r}"
        )


@then(parsers.parse('stderr contains the literal substring "{text}"'))
def assert_stderr_contains_literal(text, ctx):
    """Assert that stderr contains the exact literal string (no $HOME expansion)."""
    result = ctx.get("result")
    assert result is not None, "No result in ctx"
    stderr = result.stderr if hasattr(result, "stderr") else ""
    assert text in stderr, (
        f"Expected literal substring {text!r} in stderr, got: {stderr!r}"
    )


@then(parsers.parse('the FakeDockerDriver records that no docker run command was issued for "{container_name}"'))
def assert_no_docker_run(container_name, ctx, fake_driver):
    """Assert that no docker run was issued for the named container."""
    run_cmd = fake_driver.run_command_for_container(container_name)
    assert not run_cmd, (
        f"Expected no docker run for {container_name!r}, but got: {run_cmd!r}"
    )


@then(parsers.parse('the FakeDockerDriver records that a docker run was issued for "{container_name}"'))
def assert_docker_run_was_issued(container_name, ctx, fake_driver):
    """Assert that a docker run was recorded for the named container."""
    run_cmd = fake_driver.run_command_for_container(container_name)
    assert run_cmd, (
        f"Expected a docker run command for {container_name!r}, but none was recorded.\n"
        f"All run commands: {fake_driver._run_commands_by_container!r}"
    )


@then(parsers.parse('the FakeDockerDriver records that no exec_run was issued against "{container_name}" copying any path ending in "{path_suffix}" to "{dest}"'))
def assert_no_exec_cp_with_suffix(container_name, path_suffix, dest, ctx, fake_driver):
    """
    Assert that no exec_run cp command was issued where the source ends with path_suffix
    and the destination is dest.
    """
    matching_calls = [
        c for c in fake_driver.exec_calls
        if c.container == container_name
        and len(c.command) == 3
        and c.command[0] == "cp"
        and c.command[1].endswith(path_suffix)
        and c.command[2] == dest
    ]
    assert not matching_calls, (
        f"Expected no exec_run('cp <path ending in {path_suffix!r}> {dest}') "
        f"against {container_name!r}, but found: {[c.command for c in matching_calls]!r}"
    )


@then(parsers.parse('the FakeDockerDriver records that a tmux new-session exec_run was issued against "{container_name}"'))
def assert_tmux_new_session_issued(container_name, ctx, fake_driver):
    """Assert that a tmux new-session exec_run was recorded against the container."""
    tmux_calls = [
        c for c in fake_driver.exec_calls
        if c.container == container_name
        and len(c.command) >= 3
        and c.command[:3] == ["tmux", "new-session", "-d"]
    ]
    assert tmux_calls, (
        f"Expected a 'tmux new-session -d' exec_run against {container_name!r}, "
        f"but none was recorded.\n"
        f"Recorded exec calls: {[(c.container, c.command) for c in fake_driver.exec_calls]!r}"
    )


@then("the beads issue_prefix configured inside the container's .beads is "
      "non-empty and equals the repo's committed prefix")
def assert_beads_prefix_is_committed(ctx, fake_driver):
    """The configured prefix must be the COMMITTED prefix, NOT name-derived.

    For shopsystem-bc-launcher the committed prefix is 'bclaunch' while
    name-derivation yields 'bclauncher'; this step fails on a name-derived
    mismatch (lead-rply DEFECT 1).
    """
    from bc_launcher.controller import beads_prefix_for
    container_name = ctx["container_name"]
    bc_name = ctx["bc_name"]
    expected = ctx["committed_beads_prefix"]
    configured = fake_driver.beads_prefix(container_name)
    assert configured, (
        f"Expected a non-empty beads issue_prefix configured in "
        f"{container_name!r}, got {configured!r}"
    )
    assert configured == expected, (
        f"Expected the COMMITTED beads issue_prefix {expected!r} for BC "
        f"{bc_name!r}, got {configured!r}"
    )
    # Non-vacuity guard: the committed prefix this scenario pins must differ
    # from the name-derived prefix, so adopting-the-committed-prefix is a real
    # behavioural distinction and not accidentally satisfied by name-derivation.
    assert expected != beads_prefix_for(bc_name), (
        f"Scenario is vacuous: committed prefix {expected!r} equals the "
        f"name-derived prefix for {bc_name!r}; pick a BC whose committed "
        f"registry carries a prefix the BC name does not imply."
    )


@then("the committed beads registry is imported into the container's Dolt "
      "working set")
def assert_committed_registry_imported(ctx, fake_driver):
    """lead-kjv7 DEFECT 2 — provisioning must run an explicit `bd import`.

    Setting the issue_prefix alone does NOT import the committed registry into
    the embedded-Dolt working set (the empirical failure: `embeddeddolt/`
    absent, `bd ready`/`bd create` → "no beads database found").  The launcher
    must run `bd import` of the materialized registry.
    """
    container_name = ctx["container_name"]
    import_calls = [
        c for c in fake_driver.exec_calls
        if c.container == container_name
        and c.command[:2] == ["bd", "import"]
    ]
    assert import_calls, (
        "Expected a `bd import` exec call to import the committed registry "
        f"into the Dolt working set of {container_name!r}; the launcher must "
        "not rely on `bd config set issue_prefix` to side-effect-import "
        "(lead-kjv7 DEFECT 2)"
    )
    assert fake_driver.beads_working_set_provisioned(container_name), (
        f"Dolt working set not provisioned in {container_name!r} after "
        "provisioning"
    )


@when(parsers.parse('bc-container launch starts the agent for BC name '
                    '"{bc_name}"'))
def launch_starts_agent(bc_name, ctx, controller, fake_driver, tmp_path):
    _agent_vault_launch(
        ctx, controller, fake_driver, tmp_path, bc_name,
        startup_prompt="please begin your session",
        broker=ctx.get("agent_vault_broker"),
        dsn=ctx.get("shopmsg_dsn"),
    )


@then("the launch result is a failure naming the shop-templates update error")
def then_launch_result_failure_names_update(ctx):
    result = ctx["result"]
    assert result.exit_code != 0, (
        "Expected launch to FAIL when the skill-refresh exec failed, but it "
        f"returned success (exit 0). A failed `shop-templates update` must "
        "surface a real error and fail the launch (lead-q5k7 criterion B)."
    )
    assert "shop-templates update" in (result.stderr or ""), (
        "Launch failed but its stderr does not name the shop-templates "
        f"update error; stderr={result.stderr!r}"
    )


@then(parsers.parse(
    'the launch points git at that CA file and the proxied clone of '
    '"{bc_name}" proceeds and completes its TLS handshake with no '
    '"{err}" error'
))
def s70_assert_clone_proceeds(bc_name, err, ctx):
    """Positive example: a passing CA validation points git at the CA and the
    clone proceeds (mirrors controller.py:1469-1487)."""
    assert ctx["s70_git_pointed_at_ca"], (
        "on a valid cert the launch must point git at the CA file and let "
        "the proxied clone proceed (the REAL validation passed)."
    )
    assert ctx["s70_clone_runs"], (
        "the proxied clone must proceed after a passing CA validation."
    )


@then(parsers.parse(
    'the launch refuses to point git at the CA and the proxied clone does '
    'not run'
))
def s70_assert_clone_refused(ctx):
    """Negative example: a failed CA validation refuses to point git at the CA
    and the clone does not run (mirrors controller.py:1469-1479)."""
    assert not ctx["s70_git_pointed_at_ca"], (
        "on a marker-less cert the launch must REFUSE to point git at the "
        "CA (the REAL validation failed loud)."
    )
    assert not ctx["s70_clone_runs"], (
        "the proxied clone must NOT run after a failed CA validation."
    )


@given(parsers.parse(
    'bc-container launch is run for BC name "{bc_name}" with work id '
    '"{work_id}" on the fabro orchestrator launch path selected by '
    '"--orchestrator fabro"'))
def cadr_launch_fabro(bc_name, work_id, ctx, fake_driver, controller, tmp_path):
    """Drive the REAL launcher on the fabro orchestrator path, resolving the
    launch_path exactly as the CLI's `--orchestrator fabro` flag does — parse
    the canonical CLI surface so the flag->launch_path resolution is exercised,
    then drive controller.launch with the resolved launch_path + work_id."""
    parser = _cadr_build_parser()
    args = parser.parse_args(
        ["launch", bc_name, "--orchestrator", "fabro", "--work-id", work_id]
    )
    assert args.orchestrator == "fabro"
    launch_path = (
        _CADR_LAUNCH_PATH_FABRO
        if (args.orchestrator == "fabro" or getattr(args, "fabro_path", False))
        else _CADR_LAUNCH_PATH_TMUX
    )
    assert launch_path == _CADR_LAUNCH_PATH_FABRO
    manifest_path = _cadr_write_manifest(tmp_path, bc_name)
    result = controller.launch(
        bc_name=bc_name,
        repo_url=f"https://github.com/shopsystem/{bc_name}.git",
        manifest_path=manifest_path,
        credential_home=ctx.get("credential_home"),
        launch_path=launch_path,
        work_id=args.work_id,
    )
    assert result.exit_code == 0, (
        f"fabro-path launch failed: stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    ctx["cadr_result"] = result
    ctx["cadr_driver"] = fake_driver
    ctx["cadr_bc_name"] = bc_name
    ctx["cadr_work_id"] = work_id
    ctx["container_name"] = f"bc-{bc_name}"


@given(parsers.parse(
    'the container "{container_name}" is running on the pinned bc-base image '
    'carrying the self-contained fabro def at "{def_dir}" (scenario 75, '
    '@scenario_hash:{h75}) with the started anthropic-oauth-shim and fabro\'s '
    'anthropic "base_url" wired to it (scenario 76, @scenario_hash:{h76})'))
def cadr_container_running(container_name, def_dir, h75, h76, ctx, fake_driver):
    assert fake_driver.is_running(container_name), (
        f"Expected {container_name!r} to be running after the fabro-path launch."
    )
    ctx["container_name"] = container_name


@then(parsers.parse(
    'no tmux "agent" send-keys session and no "claude" engage is started on '
    'this path, the engage tier being REPLACED by the fabro run-graph entry '
    'rather than added alongside it (ADR-050 D3), reproducing '
    'fabro-orchestration/01 (@scenario_hash:{h01}) via the real bc-container '
    "launch path"))
def cadr_no_tmux_no_claude(h01, ctx):
    # REPLACED, not added: on the fabro path the launcher issues NO tmux
    # `agent` send-keys and NO `claude` engage — the engage tier is the fabro
    # run-graph entry alone. Bind to the launcher's ACTUAL recorded execs.
    agent_send_keys = _cadr_tmux_agent_send_keys(ctx)
    assert agent_send_keys == [], (
        "fabro path must start NO tmux 'agent' send-keys session; the launcher "
        f"issued {[c.command for c in agent_send_keys]!r}"
    )
    claude = _cadr_claude_engage_send_keys(ctx)
    assert claude == [], (
        "fabro path must start NO 'claude' engage; the launcher issued "
        f"{[c.command for c in claude]!r}"
    )
    # And the fabro run-graph entry IS present (the replacement, not an
    # absence of engage altogether).
    assert _cadr_fabro_server_calls(ctx), "fabro server start must be present"
    assert _cadr_fabro_run_calls(ctx), "fabro run must be present"


@when(_L3ZZU_STEP['acp_when'])
def l3zzu_inspect_dispatch_node(ctx):
    ctx["l3zzu_graph"] = _b3f0_dispatcher_graph_text()


@then(_L3ZZU_STEP['acp_then_kind'])
def l3zzu_dispatch_is_acp(ctx):
    body = _l3zzu_dispatch_body(ctx)
    # backend="acp": fabro drives the node through the agent-client-protocol
    # backend (v0.11.1 JSON-RPC over stdio), not the native command executor.
    assert re.search(r'backend\s*=\s*"acp"', body), (
        f'the `dispatch` node must carry backend="acp"; body:\n{body}'
    )
    # exactly one of acp.command (a shell) OR acp.config (a JSON stdio config)
    # names the external ACP process fabro launches.
    assert ("acp.command" in body) or ("acp.config" in body), (
        "the ACP dispatch node must carry an acp.command (shell such as "
        '"python3 dispatch_acp_agent.py") OR an acp.config (JSON stdio config) '
        f"attr; body:\n{body}"
    )


@then(_L3ZZU_STEP['acp_then_notnative'])
def l3zzu_dispatch_not_native(ctx):
    body = _l3zzu_dispatch_body(ctx)
    # The pre-fix context-blind native command dispatch is ABSENT: the ACP node
    # is neither a `script=` node nor a `shape=parallelogram` command node.
    assert "script=" not in body, (
        "the ACP dispatch node must NOT be a native `script=` command node "
        f"(the pre-fix context-blind dispatch must be absent); body:\n{body}"
    )
    assert "shape=parallelogram" not in body, (
        "the ACP dispatch node must NOT be a native shape=parallelogram command "
        f"node; body:\n{body}"
    )


@then(_L3ZZU_STEP['acp_then_receive'])
def l3zzu_dispatch_receives_context(ctx):
    graph = ctx.get("l3zzu_graph") or _b3f0_dispatcher_graph_text()
    # The poll -> dispatch edge feeds the ACP node the context poll yielded.
    pairs = {(s, d) for s, d, _a in _b3f0_dispatcher_edges(graph)}
    assert ("poll", "dispatch") in pairs, (
        "the ACP dispatch node must be wired to receive the poll context via a "
        f"poll -> dispatch edge; edges={pairs!r}"
    )
    # Its decision contract RECEIVES the pending work ids AND the in-flight run
    # state as its two inputs (context-in).
    mod = _l3zzu_load_acp_agent()
    assert hasattr(mod, "decide"), (
        "the ACP agent must expose a `decide` decision contract (context-in / "
        "decisions-out)"
    )
    params = list(_l3zzu_inspect.signature(mod.decide).parameters)
    assert len(params) >= 2, (
        "decide must RECEIVE the pending inbox work ids AND the in-flight run "
        f"state as its input; signature params: {params!r}"
    )


@then(_L3ZZU_STEP['acp_then_return'])
def l3zzu_dispatch_returns_decisions(ctx):
    mod = _l3zzu_load_acp_agent()
    decisions = mod.decide(["lead-a1"], set())
    assert isinstance(decisions, list) and decisions, (
        "decide must RETURN a structured list of dispatch decisions (decisions-out)"
    )
    d = decisions[0]
    assert isinstance(d, dict) and "work_id" in d and "action" in d, (
        "each returned decision must be a structured {work_id, action} record "
        f"the loop consumes to spawn children; got: {d!r}"
    )
    assert d["action"] in ("SPAWN", "SKIP"), (
        f"a decision's action must be SPAWN or SKIP; got {d['action']!r}"
    )


@given(_L3ZZU_IDEMP['given_container'])
def l3zzu_idemp_container(ctx, fake_driver, controller, tmp_path):
    _odd9_drive_fabro_launch(_ODD9_BC, ctx, fake_driver, controller, tmp_path,
                             work_id=None)
    assert fake_driver.is_running("bc-shopsystem-messaging"), (
        "Expected bc-shopsystem-messaging to be running after the fabro-path launch."
    )
    ctx["l3zzu_graph"] = _b3f0_dispatcher_graph_text()


@given(_L3ZZU_IDEMP['given_context'])
def l3zzu_idemp_context(ctx):
    # W is pending AND its prior child is still running (has not emitted
    # work_done) -> W is in the in-flight run state.
    ctx["l3zzu_pending"] = ["W"]
    ctx["l3zzu_inflight"] = {"W"}


@when(_L3ZZU_IDEMP['when'])
def l3zzu_idemp_inspect(ctx):
    ctx["l3zzu_agent"] = _l3zzu_load_acp_agent()


@then(_L3ZZU_IDEMP['then_skip'])
def l3zzu_idemp_skip(ctx):
    agent = ctx["l3zzu_agent"]
    decisions = agent.decide(ctx["l3zzu_pending"], ctx["l3zzu_inflight"])
    by_id = {d["work_id"]: d["action"] for d in decisions}
    assert by_id.get("W") == "SKIP", (
        "for a work id W whose prior child is still IN FLIGHT the ACP decision "
        "must be SKIP re-dispatch (no second child, the two children cannot "
        f"collide on the shared per-W worktree); decisions={decisions!r}"
    )
    spawns_for_w = [d for d in decisions
                    if d["work_id"] == "W" and d["action"] == "SPAWN"]
    assert not spawns_for_w, (
        f"NO SPAWN may be returned for the in-flight work id W; got {spawns_for_w!r}"
    )


@then(_L3ZZU_IDEMP['then_spawn'])
def l3zzu_idemp_spawn(ctx):
    agent = ctx["l3zzu_agent"]
    # V is a genuinely unstarted work id (NO live child); W is still in flight.
    decisions = agent.decide(["W", "V"], {"W"})
    by_id = {d["work_id"]: d["action"] for d in decisions}
    assert by_id.get("V") == "SPAWN", (
        f"a pending work id V with NO live child must be SPAWNed; decisions={decisions!r}"
    )
    assert by_id.get("W") == "SKIP", (
        f"the in-flight work id W stays SKIP alongside V; decisions={decisions!r}"
    )
    # EXACTLY ONCE: once V's child is live (tracked in-flight from the spawn), a
    # later cycle with V still pending decides SKIP.
    tracker = agent.DispatchTracker()
    c1 = tracker.cycle(["W", "V"], observed_in_flight={"W"})
    assert any(d["work_id"] == "V" and d["action"] == "SPAWN" for d in c1), (
        f"cycle 1 must SPAWN the unstarted V; got {c1!r}"
    )
    c2 = tracker.cycle(["V"])  # V now tracked in-flight from cycle 1
    assert not any(d["work_id"] == "V" and d["action"] == "SPAWN" for d in c2), (
        "V must be dispatched EXACTLY ONCE: the tracker must SKIP V on the next "
        f"cycle once its child is in flight; cycle-2 decisions={c2!r}"
    )


@then(_L3ZZU_IDEMP['then_negctl'])
def l3zzu_idemp_negctl(ctx):
    agent = ctx["l3zzu_agent"]

    # The pre-fix NATIVE command dispatch was context-blind: it re-dispatched
    # EVERY still-pending id each cycle, carrying NO in-flight skip.  Modelled
    # here to show the duplicate-spawn it produced.
    def prefix_native_dispatch(pending_ids):
        return [{"work_id": w, "action": "SPAWN"} for w in pending_ids]

    pre_c1 = prefix_native_dispatch(["W"])
    pre_c2 = prefix_native_dispatch(["W"])  # W still pending (slow child)
    prefix_spawns = [d for d in (pre_c1 + pre_c2) if d["action"] == "SPAWN"]
    assert len(prefix_spawns) == 2, (
        "the pre-fix native command dispatch must re-dispatch a still-pending W "
        f"every cycle (2 duplicate spawns across 2 cycles); got {prefix_spawns!r}"
    )

    # The ACP node's in-flight tracking ELIMINATES that duplicate-spawn: W is
    # spawned exactly once across the same two cycles.
    tracker = agent.DispatchTracker()
    tracker.cycle(["W"])           # cycle 1: SPAWN W (now in flight)
    acp_c2 = tracker.cycle(["W"])  # cycle 2: SKIP W (still in flight)
    assert not any(d["work_id"] == "W" and d["action"] == "SPAWN" for d in acp_c2), (
        "unlike the pre-fix native node, the ACP in-flight tracking must NOT "
        f"re-dispatch W on the next cycle; cycle-2 decisions={acp_c2!r}"
    )


@given(_L3ZZU_DELIVERY['given_container'])
def l3zzu_delivery_container(ctx, fake_driver, controller, tmp_path):
    _odd9_drive_fabro_launch(_ODD9_BC, ctx, fake_driver, controller, tmp_path,
                             work_id=None)
    assert fake_driver.is_running("bc-shopsystem-messaging"), (
        "Expected bc-shopsystem-messaging to be running after the fabro-path launch."
    )
    ctx["l3zzu_graph"] = _b3f0_dispatcher_graph_text()


@given(_L3ZZU_DELIVERY['given_spawn'])
def l3zzu_delivery_spawn_decision(ctx):
    agent = _l3zzu_load_acp_agent()
    ctx["l3zzu_agent"] = agent
    ctx["l3zzu_w"] = "W"
    decisions = agent.decide(["W"], set())
    by_id = {d["work_id"]: d["action"] for d in decisions}
    assert by_id.get("W") == "SPAWN", (
        f"a pending W with no live child must decide SPAWN; decisions={decisions!r}"
    )


@when(_L3ZZU_DELIVERY['when'])
def l3zzu_delivery_inspect(ctx):
    ctx.setdefault("l3zzu_agent", _l3zzu_load_acp_agent())
    ctx.setdefault("l3zzu_w", "W")


@then(_L3ZZU_DELIVERY['then_overlay'])
def l3zzu_delivery_overlay(ctx):
    agent = ctx["l3zzu_agent"]
    assert hasattr(agent, "materialize_child_config"), (
        "the ACP agent must materialize a per-child config carrying the concrete "
        "WORK_ID for each SPAWN decision (delivery contract)"
    )
    cfg = agent.materialize_child_config("W")
    assert "[run.environment.env]" in cfg, (
        f"the materialized child config must carry a [run.environment.env] overlay; config:\n{cfg}"
    )
    # The CONCRETE per-child work id is written as WORK_ID="W" (from the decision,
    # not a fixed literal), so the child receives its OWN work id.
    assert re.search(r'WORK_ID\s*=\s*"W"', cfg), (
        f'the overlay must set the concrete WORK_ID="W" for this child; config:\n{cfg}'
    )
    ctx["l3zzu_child_cfg"] = cfg
    # per-child: a DIFFERENT work id yields a DIFFERENT concrete WORK_ID.
    other = agent.materialize_child_config("V")
    assert re.search(r'WORK_ID\s*=\s*"V"', other), (
        f'materialize must carry the CONCRETE per-child work id (V for V); config:\n{other}'
    )


@then(_L3ZZU_DELIVERY['then_detached'])
def l3zzu_delivery_detached(ctx):
    agent = ctx["l3zzu_agent"]
    assert hasattr(agent, "spawn_command"), (
        "the ACP agent must expose the detached spawn command for a SPAWN decision"
    )
    cmd = agent.spawn_command("W")
    cmd_s = " ".join(cmd) if isinstance(cmd, (list, tuple)) else str(cmd)
    assert "fabro run" in cmd_s, (
        f"the spawn must issue `fabro run`; command:\n{cmd_s}"
    )
    assert "--detach" in cmd_s, (
        f"the child must be spawned DETACHED (--detach) so the dispatch step does "
        f"not block before the wait -> poll back-edge; command:\n{cmd_s}"
    )
    # `fabro run` targets a per-child .toml entrypoint carrying the concrete W.
    assert re.search(r"fabro run [^\s]*W[^\s]*\.toml", cmd_s), (
        f"the spawn must `fabro run` a per-child .toml naming the concrete W; command:\n{cmd_s}"
    )


@then(_L3ZZU_DELIVERY['then_child_reaches'])
def l3zzu_delivery_child_reaches(ctx):
    cfg = ctx["l3zzu_child_cfg"]
    # The materialized child config applies the UNCHANGED ADR-051 workflow.fabro.
    assert re.search(r'graph\s*=\s*"workflow\.fabro"', cfg), (
        f"the child config must apply the ADR-051 workflow.fabro child def; config:\n{cfg}"
    )
    wf = _ky63_def_asset_root() / "workflow.fabro"
    assert wf.is_file(), "the ADR-051 workflow.fabro child def must ship in the bundle"
    # The [run.environment.env] overlay is the PROVEN channel that reaches a
    # native script= node's env (the child workflow.toml documents it); the ACP
    # node materializes WORK_ID via that SAME channel, and NOT via `-I WORK_ID`
    # (which does NOT reach the child's native script= env).
    child_toml = (_ky63_def_asset_root() / "workflow.toml").read_text()
    assert "[run.environment.env]" in child_toml and "WORK_ID" in child_toml, (
        "the child workflow.toml must document the [run.environment.env] WORK_ID "
        "overlay (the proven native-script delivery channel)"
    )
    assert "-I WORK_ID" not in cfg, (
        "the ACP node must deliver WORK_ID via the [run.environment.env] overlay, "
        f"NOT `-I WORK_ID` (which does not reach the child native script env); config:\n{cfg}"
    )
