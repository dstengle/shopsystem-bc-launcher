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
