"""Step definitions for the remote-backed beads schema-skew heal reaching a
WORKING local current-schema DB WITHOUT the ``--discard-remote`` path and
WITHOUT diverging the remote (scenario fdfaaa78dc322bbc, @origin:lead-16zo).

Binds the fdfaaa78dc322bbc row of
features/bc_container_beads_schema_skew_heal.feature.  ADDITIVE to the
lead-915f schema-skew family in tests/steps/schema_skew.py; retires/supersedes
NOTHING.

v0.3.67 prod defect (lead-oqaw): the baked lead-915f self-heal ran
``bd init --from-jsonl`` while ``sync.remote`` was still configured in
``.beads/config.yaml`` and the remote carried Dolt history, so bd's
remote-history guard fired — "remote has Dolt history and you selected local
history without --discard-remote" — and the heal either hard-FAILED exit 10 or
would have had to drive the history-REPLACING ``--discard-remote`` branch,
diverging the BC's beads remote.  Either way the BC came up with DEAD beads.

Like the lead-915f executed-ordering steps in tests/steps/schema_skew.py, these
steps EXECUTE the live ``_schema_skew_heal_script`` body against a fixture
``.beads`` tree with ``bd`` stubbed to faithfully model bd's remote-history
guard (exit 10 on a reinit against a config'd remote that carries history,
UNLESS ``--discard-remote`` is passed) — real teeth, no live container/remote.
"""
from __future__ import annotations

import subprocess

from pytest_bdd import given, then, when

from bc_launcher.controller import (
    CONTAINER_WORKSPACE,
    _schema_skew_heal_script,
)

_BEADS_REMOTE = "git+https://github.com/dstengle/shopsystem-bc-launcher-beads.git"


def _heal_body(script: str) -> str:
    """Strip the leading ``set -e; cd <WS>; `` so the heal body runs in a
    fixture workspace as cwd, KEEPING ``set -e`` so a genuine mid-heal failure
    (e.g. the exit-10 remote-history guard) aborts the heal."""
    prefix = f"set -e; cd {CONTAINER_WORKSPACE}; "
    assert script.startswith(prefix), script[:120]
    return "set -e; " + script[len(prefix):]


def _bd_stub(probe: str) -> str:
    """A ``bd`` shell stub modelling bd's remote-history guard.

    - ``bd ready``   : exit 0 iff ``.beads/HEALTHY`` present.
    - ``bd export``  : writes a capture, records ordering, exit 0.
    - ``bd init``    : models the remote-history guard (bd upstream #4259 /
                       "remote has Dolt history and you selected local history
                       without --discard-remote").  If ``.beads/config.yaml``
                       STILL configures ``sync.remote`` AND the remote carries
                       history (``.beads/REMOTE_HAS_HISTORY``) AND
                       ``--discard-remote`` was NOT passed -> print the guard
                       message and exit 10.  If ``--discard-remote`` WAS passed
                       -> record the divergence and succeed.  Otherwise (remote
                       stripped from config) -> CREATE-FRESH at current schema.
    - ``bd dolt push``: the durable reseed; DEFERRED on the lead-tc38 brokered
                       dolt-push credential path -> fails on the cred gap, so it
                       does NOT land / does NOT diverge the remote.
    """
    return (
        f'PROBE="{probe}"; '
        'bd() { '
        '  if [ "$1" = "ready" ]; then '
        '    if [ -f .beads/HEALTHY ]; then return 0; else return 1; fi; '
        '  fi; '
        '  if [ "$1" = "export" ]; then '
        '    printf "{\\"exported\\": true}\\n"; return 0; '
        '  fi; '
        '  if [ "$1" = "init" ]; then '
        '    _remote=no; '
        '    if [ -f .beads/config.yaml ] && grep -q "^sync\\.remote:" .beads/config.yaml; then _remote=yes; fi; '
        '    _discard=no; '
        '    for _a in "$@"; do if [ "$_a" = "--discard-remote" ]; then _discard=yes; fi; done; '
        '    if [ "$_remote" = yes ] && [ -f .beads/REMOTE_HAS_HISTORY ] && [ "$_discard" = no ]; then '
        '      echo "Error: remote has Dolt history and you selected local history without --discard-remote" >&2; '
        '      printf exit10 > "$PROBE/at_init"; return 10; '
        '    fi; '
        '    if [ "$_discard" = yes ]; then printf discard > "$PROBE/init_discard"; fi; '
        '    mkdir -p .beads/embeddeddolt; '
        '    : > .beads/HEALTHY; '
        '    wc -l < .beads/issues.jsonl | tr -d " " > .beads/REBUILT_COUNT; '
        '    printf current > .beads/SCHEMA_VERSION; '
        '    printf init > "$PROBE/at_init"; return 0; '
        '  fi; '
        '  if [ "$1" = "dolt" ] && [ "$2" = "push" ]; then '
        '    printf failed-cred-gap > "$PROBE/at_push"; return 1; '
        '  fi; '
        '  return 0; '
        '}; '
    )


_CONFIG_YAML = (
    "# Beads Configuration File\n"
    "# issue-prefix: \"\"\n"
    'sync.remote: "git+https://github.com/dstengle/shopsystem-bc-launcher-beads.git"\n'
)


def _setup_wall(ctx, tmp_path, count=5):
    """A remote-backed DB at an OLD schema behind the baked bd's target: a
    partial old ``.beads/embeddeddolt`` present, NO HEALTHY marker (bd ready
    fails), ``.beads/config.yaml`` STILL configuring ``sync.remote``, the
    remote carrying Dolt history (REMOTE_HAS_HISTORY), committed issues.jsonl
    carrying ``count`` issues."""
    ws = ctx.setdefault("ws", tmp_path / "ws")
    beads = ws / ".beads"
    beads.mkdir(parents=True, exist_ok=True)
    probe = ctx.setdefault("probe", tmp_path / "probe")
    probe.mkdir(parents=True, exist_ok=True)
    (beads / "embeddeddolt").mkdir(exist_ok=True)
    (beads / "embeddeddolt" / "OLD_SCHEMA").write_text("v32 remote-backed\n")
    (beads / "REMOTE_HAS_HISTORY").write_text("v32\n")
    (beads / "config.yaml").write_text(_CONFIG_YAML)
    lines = [
        f'{{"_type":"issue","id":"bclaunch-{i}","title":"issue {i}",'
        f'"status":"open","priority":1}}'
        for i in range(1, count + 1)
    ]
    (beads / "issues.jsonl").write_text("\n".join(lines) + "\n")
    ctx["committed_count"] = count
    ctx.setdefault("shop_type", "bc")


def _run_heal(ctx):
    ws = ctx["ws"]
    probe = ctx["probe"]
    script = _schema_skew_heal_script(_BEADS_REMOTE, ctx.get("shop_type", "bc"))
    ctx["script"] = script
    body = _heal_body(script)
    result = subprocess.run(
        ["bash", "-c", _bd_stub(str(probe)) + body],
        cwd=str(ws),
        capture_output=True,
        text=True,
    )
    ctx["result"] = result
    return result


def _probe(ctx, name):
    p = ctx["probe"] / name
    return p.read_text() if p.exists() else None


# ---------------------------------------------------------------------------
# GIVEN
# ---------------------------------------------------------------------------

@given('a BC standup clones a remote-backed beads DB whose Dolt data sits at '
       'an OLD schema behind the baked bd\'s CURRENT target schema, so '
       '"bd bootstrap" fails on the bd upstream #4259 migration refusal')
def given_wall(ctx, tmp_path):
    _setup_wall(ctx, tmp_path)


@given('the committed ".beads/issues.jsonl" carries a known issue count that '
       'is the schema-independent source of truth')
def given_committed_count(ctx):
    beads = ctx["ws"] / ".beads"
    assert (beads / "issues.jsonl").exists()
    assert ctx["committed_count"] == len(
        (beads / "issues.jsonl").read_text().splitlines()
    )


# ---------------------------------------------------------------------------
# WHEN
# ---------------------------------------------------------------------------

@when("the standup's beads self-heal runs against that remote-backed "
      "schema-skew wall")
def when_self_heal(ctx):
    _run_heal(ctx)


# ---------------------------------------------------------------------------
# THEN
# ---------------------------------------------------------------------------

@then('the self-heal rebuilds a fresh local current-schema dolt DB from the '
      'committed ".beads/issues.jsonl" WITHOUT driving the "--discard-remote" '
      'branch, so it does NOT fail exit 10 on the "remote has Dolt history and '
      'you selected local history without --discard-remote" guard')
def then_rebuilds_without_discard(ctx):
    script = ctx["script"]
    assert "--discard-remote" not in script, (
        "the heal must NEVER drive bd's --discard-remote branch — that path is "
        "history-replacing and diverges the BC's beads remote (lead-oqaw)"
    )
    assert "bd init --from-jsonl .beads/issues.jsonl" in script, (
        "the heal must rebuild from the committed issues.jsonl (lead-oqaw)"
    )
    assert _probe(ctx, "at_init") == "init", (
        "the executed heal must reach a SUCCESSFUL from-jsonl rebuild, not the "
        "exit-10 remote-history guard; got at_init="
        f"{_probe(ctx, 'at_init')!r} exit={ctx['result'].returncode} "
        f"stderr={ctx['result'].stderr!r} (lead-oqaw)"
    )
    assert _probe(ctx, "init_discard") is None, (
        "the from-jsonl rebuild must NOT be driven with --discard-remote "
        "(lead-oqaw)"
    )
    assert ctx["result"].returncode == 0, (
        "the heal must not fail exit 10 on the remote-history guard; exit "
        f"{ctx['result'].returncode}: {ctx['result'].stderr!r} (lead-oqaw)"
    )
    assert "without --discard-remote" not in ctx["result"].stderr, (
        "the heal must not surface bd's remote-history guard error (lead-oqaw)"
    )


@then('after the heal "bd ready" exits zero so the BC comes up with LIVE beads '
      'rather than dead beads, and the rebuilt DB\'s issue count equals the '
      'count committed in ".beads/issues.jsonl" at the baked bd\'s CURRENT '
      'target schema')
def then_ready_and_parity(ctx):
    beads = ctx["ws"] / ".beads"
    assert (beads / "HEALTHY").exists(), (
        "after the heal the rebuilt DB must be healthy so `bd ready` exits "
        "zero — LIVE beads, not dead beads (lead-oqaw)"
    )
    ready = subprocess.run(
        ["bash", "-c", _bd_stub(str(ctx["probe"])) + "bd ready"],
        cwd=str(ctx["ws"]), capture_output=True, text=True,
    )
    assert ready.returncode == 0, (
        "`bd ready` must exit zero against the rebuilt DB (lead-oqaw)"
    )
    rebuilt = (beads / "REBUILT_COUNT").read_text().strip()
    assert int(rebuilt) == ctx["committed_count"], (
        f"rebuilt count {rebuilt} != committed {ctx['committed_count']} "
        "(lead-oqaw)"
    )
    assert (beads / "SCHEMA_VERSION").read_text().strip() == "current", (
        "the rebuilt DB must be at the baked bd's CURRENT target schema "
        "(lead-oqaw)"
    )


@then('the heal reaches this working local state WITHOUT diverging the BC\'s '
      'beads remote — no history-replacing push and no "--discard-remote" — so '
      'the durable remote reseed (@scenario_hash:df748234563bdedb / lead-mv16) '
      'remains DEFERRED on lead-tc38 and every relaunch heals locally rather '
      'than re-breaking on the remote-history guard')
def then_no_divergence(ctx):
    script = ctx["script"]
    # No --discard-remote anywhere: the heal never drives the history-replacing
    # branch of bd's remote-history guard.
    assert "--discard-remote" not in script, (
        "no --discard-remote: the heal must not history-replace the remote "
        "(lead-oqaw)"
    )
    assert _probe(ctx, "init_discard") is None
    # The durable reseed remains DEFERRED (lead-tc38): the reseed push does NOT
    # land, so no history-replacing push diverges the remote.
    assert _probe(ctx, "at_push") != "complete", (
        "the durable remote reseed must remain DEFERRED on lead-tc38 — no "
        "history-replacing push may land / diverge the remote (lead-oqaw)"
    )
    # The strip of sync.remote is TEMPORARY: config.yaml is RESTORED so the
    # remote stays configured for the deferred reseed and future launches.
    restored = (ctx["ws"] / ".beads" / "config.yaml").read_text()
    assert "sync.remote:" in restored, (
        "the heal must RESTORE sync.remote to .beads/config.yaml after the "
        "reinit-local rebuild — the strip is temporary, not a divergence "
        "(lead-oqaw)"
    )
    # A relaunch heals locally again (idempotent): re-running the heal against
    # the restored state still reaches a working local DB without exit 10.
    ctx2 = {"ws": ctx["ws"], "probe": ctx["probe"], "shop_type": "bc",
            "committed_count": ctx["committed_count"]}
    (ctx["ws"] / ".beads" / "HEALTHY").unlink(missing_ok=True)
    relaunch = _run_heal(ctx2)
    assert relaunch.returncode == 0, (
        "every relaunch must heal locally rather than re-break on the "
        f"remote-history guard; exit {relaunch.returncode}: "
        f"{relaunch.stderr!r} (lead-oqaw)"
    )
    assert (ctx["ws"] / ".beads" / "HEALTHY").exists()
