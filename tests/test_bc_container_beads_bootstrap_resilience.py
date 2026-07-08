"""pytest-bdd binding for the bd-bootstrap resilience bugfix (lead-5k8c).

Pins two additive behaviors on the in-container bd-bootstrap step:
  1. EMPTY-REMOTE PROVISIONING — an empty `<bc>-beads` Dolt remote is
     INITIALIZED (init-and-push an initial branch/commit) then provisioned
     write-ready, instead of fatal-failing the clone.
  2. NO PRE-AGENT-START STEP MAY FATAL-STRAND — any bd-bootstrap failure
     (including a seed that could not initialize the remote) WARNS and
     proceeds to agent-start (generalizes the lead-k4k7 invariant).
"""
import subprocess

import pytest
from pytest_bdd import scenarios

scenarios("../features/bc_container_beads_bootstrap_resilience.feature")


# ---------------------------------------------------------------------------
# lead-vb6j / ROOT / GAP G follow-up — EXECUTED prefix-extraction correctness.
#
# The e3a0ec19298e7ce7 structural scenario asserts the create-fresh step's
# PRESENCE + ORDERING in `_empty_remote_seed_script`, but never EXECUTES the
# inline shell that derives the committed prefix `bd init -p` adopts.  A real
# production bug hid behind that: the extraction (a) greps `"issue_prefix"` from
# `.beads/metadata.json` — which real bd-written metadata.json does NOT carry
# (it carries `"dolt_database"`) — and (b) falls back to a GREEDY sed on
# `.beads/issues.jsonl` whose `"\(.*\)-[^-]*"` capture bleeds ACROSS quotes and
# fields, cutting at the LAST hyphen of the whole multi-field line and yielding
# a ~1000-char garbage string instead of the committed prefix.  So `bd init -p
# "<garbage>"` create-fresh's a garbage-prefixed DB and `bd create` never yields
# the committed `<prefix>-<n>` — the ROOT goal is unmet.
#
# This test ACTUALLY RUNS the extraction shell sliced from the live
# `_empty_remote_seed_script` string against REAL-SHAPED committed artifacts
# (metadata.json with `dolt_database` and NO `issue_prefix`; a multi-field
# issues.jsonl first line reproducing the garbage trigger) and asserts the
# derived prefix EQUALS the committed prefix (exact, short), NOT garbage.  RED
# against the greedy sed; GREEN after the extraction mirrors
# `committed_beads_prefix_from_registry` (quote-bounded id, before the FINAL
# hyphen) with `dolt_database` as a robust primary source.
# ---------------------------------------------------------------------------


def _extraction_fragment(script: str) -> str:
    """Slice the committed-prefix extraction shell (the statements that set
    ``$gapg_prefix``) out of the live ``_empty_remote_seed_script`` string —
    everything from the first ``gapg_prefix=`` assignment up to the
    ``bd init -p`` create-fresh step that consumes it.  The fragment reads only
    relative ``.beads/...`` paths, so it runs standalone against a fixture
    ``.beads`` tree."""
    start = script.index("gapg_prefix=")
    end = script.index("bd init", start)
    return script[start:end]


def _run_extraction(fragment: str, workspace) -> str:
    """Execute the extraction fragment in ``workspace`` and return the derived
    ``$gapg_prefix``."""
    result = subprocess.run(
        ["bash", "-c", fragment + "printf '%s' \"$gapg_prefix\""],
        cwd=str(workspace),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"extraction fragment failed: {result.stderr!r}"
    )
    return result.stdout


# A realistic MULTI-FIELD issues.jsonl first line: id FIRST, then title /
# description carrying many hyphens and embedded quotes — the exact shape whose
# greedy `"\(.*\)-[^-]*"` capture blows up to a ~1000-char garbage prefix.
_MULTIFIELD_ISSUE_LINE = (
    '{{"_type":"issue","id":"{issue_id}","title":"write the failing test for '
    'standup establishing a prefixed local dolt DB create-fresh from '
    'metadata.json BEFORE seeding (create-fresh-then-seed ordering) — scenario '
    'e3a0ec19298e7ce7","description":"RED: pin e3a0-then-seed ordering — '
    'refs/dolt/* present; bd create yields <prefix>-<n>","status":"open",'
    '"priority":1}}'
)


@pytest.mark.parametrize(
    "dolt_database, issue_id, expected_prefix",
    [
        # Primary source: metadata.json `dolt_database` (real bd shape — NO
        # `issue_prefix` key) holds the committed prefix.
        ("shopsystem_bc_launcher", "shopsystem_bc_launcher-krtb.1",
         "shopsystem_bc_launcher"),
        # The reviewer's explicit example: a shopsystem-knowledge BC must yield
        # `shopsystem_knowledge`, not a name-derived or garbage prefix.
        ("shopsystem_knowledge", "shopsystem_knowledge-a1b",
         "shopsystem_knowledge"),
    ],
)
def test_lead_vb6j_seed_prefix_extraction_from_dolt_database(
    tmp_path, dolt_database, issue_id, expected_prefix
):
    """The extraction must derive the committed prefix from metadata.json's
    `dolt_database` (real bd shape carries NO `issue_prefix`), NOT the garbage
    from a greedy issues.jsonl sed (lead-vb6j / ROOT / GAP G follow-up)."""
    from bc_launcher.controller import _empty_remote_seed_script

    beads = tmp_path / ".beads"
    beads.mkdir()
    (beads / "metadata.json").write_text(
        '{\n'
        '  "database": "dolt",\n'
        '  "backend": "dolt",\n'
        '  "dolt_mode": "embedded",\n'
        f'  "dolt_database": "{dolt_database}",\n'
        '  "project_id": "53d541df-a20b-4647-8639-ecfded13c9d3"\n'
        '}\n'
    )
    (beads / "issues.jsonl").write_text(
        _MULTIFIELD_ISSUE_LINE.format(issue_id=issue_id) + "\n"
    )
    fragment = _extraction_fragment(
        _empty_remote_seed_script(
            "git+https://github.com/dstengle/shopsystem-knowledge-beads.git"
        )
    )
    derived = _run_extraction(fragment, tmp_path)

    assert derived == expected_prefix, (
        f"extraction derived {derived!r} (len={len(derived)}), expected the "
        f"committed prefix {expected_prefix!r} — a greedy sed across the "
        "multi-field issues.jsonl line yields a long garbage string "
        "(lead-vb6j / ROOT / GAP G follow-up)"
    )
    assert len(derived) < 64, (
        f"derived prefix is {len(derived)} chars — a committed prefix is short; "
        "this is the greedy-sed garbage bug (lead-vb6j / ROOT / GAP G follow-up)"
    )


def test_lead_vb6j_seed_prefix_extraction_fallback_to_issues_jsonl(tmp_path):
    """When metadata.json carries neither `dolt_database` nor `issue_prefix`,
    the extraction must fall back to the FIRST issue id in issues.jsonl,
    quote-bounded and cut at its FINAL hyphen (mirrors
    `committed_beads_prefix_from_registry`) — NOT a greedy garbage cut
    (lead-vb6j / ROOT / GAP G follow-up)."""
    from bc_launcher.controller import _empty_remote_seed_script

    beads = tmp_path / ".beads"
    beads.mkdir()
    # metadata.json WITHOUT dolt_database / issue_prefix → forces the
    # issues.jsonl fallback (the greedy-sed bug locus).
    (beads / "metadata.json").write_text(
        '{\n  "database": "dolt",\n  "backend": "dolt",\n'
        '  "dolt_mode": "embedded"\n}\n'
    )
    (beads / "issues.jsonl").write_text(
        _MULTIFIELD_ISSUE_LINE.format(issue_id="shopsystem_bc_launcher-krtb.1")
        + "\n"
    )
    fragment = _extraction_fragment(
        _empty_remote_seed_script(
            "git+https://github.com/dstengle/shopsystem-knowledge-beads.git"
        )
    )
    derived = _run_extraction(fragment, tmp_path)

    assert derived == "shopsystem_bc_launcher", (
        f"issues.jsonl fallback derived {derived!r} (len={len(derived)}), "
        "expected 'shopsystem_bc_launcher' — the greedy `\"\\(.*\\)-[^-]*\"` "
        "sed bleeds across the multi-field line into garbage (lead-vb6j / ROOT "
        "/ GAP G follow-up)"
    )


# ---------------------------------------------------------------------------
# lead-tc38 / GAP H (ROOT, supersedes GAP G e3a0ec19298e7ce7) — the seed script
# must UNCONFIGURE sync.remote from .beads/config.yaml BEFORE `bd init -p`
# create-fresh, then RESTORE it before `bd dolt remote add`/`bd dolt push`.
#
# GAP G false-green: its create-fresh `bd init -p <prefix>` ran WHILE
# `sync.remote` was STILL configured in .beads/config.yaml to the derived
# `<owner>/<bc>-beads` remote — which EXISTS but is EMPTY of Dolt data.  With
# sync.remote configured, `bd init` (like `bd bootstrap`) CLONES the empty
# remote and HARD-FAILS "Error 1105: clone failed; remote at that url contains
# no Dolt data"; the create-fresh never happens.  GAP G's structural test
# FALSE-GREENED because its fixture OMITTED the configured-empty-remote
# precondition.  Confirmed in a real in-container launch (v0.3.56).
#
# This RED test REPLICATES that precondition — a real-shaped .beads/config.yaml
# carrying a `sync.remote: "git+https://..."` line — and EXECUTES the seed
# script's create-fresh/seed body with `bd` stubbed to RECORD the exact
# config.yaml contents at the moment each step runs.  It asserts the executed
# ordering: sync.remote ABSENT at `bd init` time (unconfigured first) and
# PRESENT again at `bd dolt remote add`/`bd dolt push` time (restored).  The
# negative control (the scenario's last And): had `bd init -p` run WHILE
# sync.remote was still configured, it would clone the empty remote and
# hard-fail — the executed at-init state proving sync.remote is gone is exactly
# what averts that pre-fix real-launch failure.
# ---------------------------------------------------------------------------

# The scaffolded sync.remote line shape in a real BC .beads/config.yaml — the
# configured-empty-remote precondition GAP G omitted (the derived
# <owner>/<bc>-beads remote that exists but is EMPTY of Dolt data).
_SYNC_REMOTE_LINE = (
    'sync.remote: "git+https://github.com/dstengle/shopsystem-knowledge-beads.git"'
)


def _seed_body_fragment(script: str) -> str:
    """Slice the create-fresh/seed body (the statements that touch
    .beads/config.yaml and run `bd init`/`bd dolt ...`) out of the live
    ``_empty_remote_seed_script`` string — from the committed-prefix extraction
    up to the final raw-git ``git ls-remote`` verification (dropped because it
    needs a live remote).  The fragment reads only relative ``.beads/...``
    paths, so it runs standalone against a fixture ``.beads`` tree."""
    start = script.index("gapg_prefix=")
    end = script.index("git ls-remote", start)
    return script[start:end]


def _run_seed_body(fragment: str, workspace, probe_dir):
    """Execute the create-fresh/seed body in ``workspace`` with ``bd`` stubbed
    to RECORD .beads/config.yaml contents at each step it runs (init /
    dolt-remote-add / dolt-push), so the test can assert the EXECUTED unconfigure
    -> init -> restore -> seed ordering rather than mere string presence."""
    prelude = (
        # Stub `bd`: snapshot config.yaml at each observed sub-command so the
        # test reads back the exact config state at bd-init and bd-dolt-push
        # time.  `BD_NON_INTERACTIVE=1 bd init ...` passes the env prefix through
        # to this function fine.
        f'PROBE="{probe_dir}"; '
        'bd() { '
        '  if [ "$1" = "init" ]; then cp .beads/config.yaml "$PROBE/at_init.yaml"; return 0; fi; '
        '  if [ "$1" = "dolt" ] && [ "$2" = "remote" ]; then cp .beads/config.yaml "$PROBE/at_remote_add.yaml"; return 0; fi; '
        '  if [ "$1" = "dolt" ] && [ "$2" = "push" ]; then cp .beads/config.yaml "$PROBE/at_push.yaml"; return 0; fi; '
        '  return 0; '
        '}; '
    )
    result = subprocess.run(
        ["bash", "-c", prelude + fragment],
        cwd=str(workspace),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"seed body fragment failed: {result.stderr!r}"
    )
    return result


def _write_configured_empty_remote_fixture(tmp_path):
    """Materialize the CONFIGURED-empty-remote precondition GAP G omitted: a
    .beads tree whose config.yaml carries a live `sync.remote` line pointing at
    the empty tracker remote, plus a real-shaped metadata.json (dolt_database)
    and issues.jsonl."""
    beads = tmp_path / ".beads"
    beads.mkdir()
    (beads / "config.yaml").write_text(
        "# Beads Configuration File\n"
        "# issue-prefix auto-detects; the tracker remote line follows\n"
        "\n"
        + _SYNC_REMOTE_LINE + "\n"
    )
    (beads / "metadata.json").write_text(
        '{\n'
        '  "database": "dolt",\n'
        '  "backend": "dolt",\n'
        '  "dolt_mode": "embedded",\n'
        '  "dolt_database": "shopsystem_bc_launcher",\n'
        '  "project_id": "53d541df-a20b-4647-8639-ecfded13c9d3"\n'
        '}\n'
    )
    (beads / "issues.jsonl").write_text(
        _MULTIFIELD_ISSUE_LINE.format(issue_id="shopsystem_bc_launcher-krtb.1")
        + "\n"
    )
    return beads


def test_lead_tc38_seed_unconfigures_sync_remote_before_bd_init_then_restores(
    tmp_path,
):
    """EXECUTED: with sync.remote CONFIGURED to the empty remote (the GAP G
    precondition), the seed body must have UNCONFIGURED sync.remote from
    .beads/config.yaml BY the time `bd init -p` runs (so create-fresh does not
    clone the empty remote and hard-fail), and RESTORED it by the time
    `bd dolt remote add`/`bd dolt push` run (lead-tc38 / GAP H)."""
    from bc_launcher.controller import _empty_remote_seed_script

    beads = _write_configured_empty_remote_fixture(tmp_path)
    probe = tmp_path / "probe"
    probe.mkdir()

    # Precondition sanity: the fixture reproduces the configured-empty-remote
    # state GAP G omitted.
    assert "sync.remote" in (beads / "config.yaml").read_text(), (
        "fixture must carry a configured sync.remote line — the precondition "
        "GAP G's false-green fixture omitted (lead-tc38 / GAP H)"
    )

    fragment = _seed_body_fragment(
        _empty_remote_seed_script(
            "git+https://github.com/dstengle/shopsystem-knowledge-beads.git"
        )
    )
    _run_seed_body(fragment, tmp_path, probe)

    at_init = probe / "at_init.yaml"
    at_remote_add = probe / "at_remote_add.yaml"
    at_push = probe / "at_push.yaml"
    assert at_init.exists(), "seed body never reached `bd init`"
    assert at_remote_add.exists(), "seed body never reached `bd dolt remote add`"
    assert at_push.exists(), "seed body never reached `bd dolt push`"

    # (1) UNCONFIGURE-BEFORE-INIT: at `bd init -p` time the sync.remote line
    #     must be GONE, so create-fresh does not clone the configured empty
    #     remote and hard-fail "contains no Dolt data" (the pre-fix failure).
    assert "sync.remote" not in at_init.read_text(), (
        "sync.remote was STILL configured when `bd init -p` ran — `bd init` "
        "would CLONE the empty remote and hard-fail 'contains no Dolt data' "
        "(this is GAP G's false-green: init ran WITH the remote configured); "
        "the seed must unconfigure sync.remote BEFORE bd init (lead-tc38 / GAP H)"
    )

    # (2) RESTORE-BEFORE-SEED: by the time the dolt remote is (re)configured and
    #     pushed, sync.remote must be back in config.yaml.
    assert "sync.remote" in at_remote_add.read_text(), (
        "sync.remote was NOT restored before `bd dolt remote add` — the seed "
        "must restore the sync.remote line after bd init (lead-tc38 / GAP H)"
    )
    assert _SYNC_REMOTE_LINE in at_push.read_text(), (
        "the ORIGINAL sync.remote line was NOT restored before `bd dolt push` "
        "— the seed must restore the exact captured line (lead-tc38 / GAP H)"
    )

    # And the final on-disk config.yaml carries the restored line (net effect:
    # the unconfigure is transient, only spanning bd init).
    assert _SYNC_REMOTE_LINE in (beads / "config.yaml").read_text(), (
        "after the seed body the sync.remote line must be restored on disk "
        "(lead-tc38 / GAP H)"
    )


def test_lead_tc38_unconfigure_ordering_in_seed_script_string(tmp_path):
    """Structural backstop for the EXECUTED ordering test: in the seed script
    the sync.remote unconfigure must appear BEFORE `bd init -p`, and the restore
    BEFORE `bd dolt remote add`/`bd dolt push` (lead-tc38 / GAP H)."""
    from bc_launcher.controller import _empty_remote_seed_script

    script = _empty_remote_seed_script(
        "git+https://github.com/dstengle/shopsystem-knowledge-beads.git"
    )
    # The unconfigure removes the sync.remote line from .beads/config.yaml.
    assert "sync.remote" in script and "config.yaml" in script, (
        "seed script must reference unconfiguring sync.remote in "
        ".beads/config.yaml before bd init (lead-tc38 / GAP H)"
    )
    i_unconfigure = script.index("config.yaml")
    i_init = script.index("bd init")
    i_remote_add = script.index("bd dolt remote add")
    i_push = script.index("bd dolt push")
    assert i_unconfigure < i_init < i_remote_add < i_push, (
        "ordering must be unconfigure(config.yaml) < bd init < bd dolt remote "
        f"add < bd dolt push; got config.yaml@{i_unconfigure}, "
        f"init@{i_init}, remote_add@{i_remote_add}, push@{i_push} "
        "(lead-tc38 / GAP H)"
    )
