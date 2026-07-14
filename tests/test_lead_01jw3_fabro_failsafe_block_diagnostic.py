"""lead-01jw.3 behavior 1 — the fabro finite-run failsafe block report is
DIAGNOSTIC, not content-free.

Empirical grounding (reconciled at the REAL mechanism, not a model):

  The fabro finite-run failsafe is the `emit_blk` node in the committed
  ``workflow.fabro``.  Every fallible node routes ``-> emit_blk
  [condition="outcome=failed"]``.  ADR-051 / lead-i0wi F2 established that
  emit_blk reports the block via a NON-consuming ``shop-msg nudge`` (a
  de-pending ``respond work_done --status blocked`` would consume-and-LOSE the
  retriable dispatch), and ``test_lead_i0wi_fabro_residuals.py`` actively
  forbids re-introducing the consuming form.  So the block report today is a
  non-consuming nudge whose ``--note`` is a GENERIC CONTENT-FREE string with an
  empty failing-node, empty reason, and empty body — the lead-01jw.3 regression
  (first observed as the stale lead-ew86 outbox row id=1712, scenario_hashes=[],
  the generic summary).

  Reconciliation: honoring scenario 629be1e0224f3a03 (the block report carries
  the failing node + a reason class + captured error context) does NOT force
  re-introducing the consuming ``respond work_done --status blocked``.  The
  diagnosis is composed INTO the non-consuming nudge ``--note``, sourced from a
  per-work run-context capture.  ADR-051 is preserved (still a non-consuming
  report; never a false complete; emit_r stays the sole complete emitter).

Fidelity: this binds to the REAL committed ``workflow.fabro`` `emit_blk` node
body via the launcher's own def-asset loader (the same structural fidelity
``test_lead_i0wi_fabro_residuals.py`` uses), and recomputes the block-only
scenario hash with the canonical ``scenarios`` tool (the same fidelity
``test_lead_bnhn_diagnostic_write_resilience_pins.py`` uses).  It is NOT a model
and NOT a shallow string-match: the three diagnostic fields must be INTERPOLATED
(shell-expanded) from the run-context capture, so a static blank cannot pass.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from bc_launcher.controller import _fabro_def_asset_root


# ---- REAL committed def helpers (shared shape with lead-i0wi) --------------

def _workflow_text() -> str:
    """The REAL committed workflow.fabro bytes (the placed def)."""
    return (_fabro_def_asset_root() / "workflow.fabro").read_text()


def _node_body(graph: str, name: str) -> str:
    """Return the ``name [ ... ]`` attribute body for a node, scanning the
    matching ``]`` quote-aware so a shell ``[ ... ]`` inside a script= string
    does not close the node early."""
    m = re.search(rf"(?m)^\s*{re.escape(name)}\s*\[", graph)
    assert m is not None, f"node {name!r} not found in workflow.fabro"
    i = m.end() - 1
    depth = 0
    inq = False
    j = i
    while j < len(graph):
        c = graph[j]
        if inq:
            if c == "\\":
                j += 2
                continue
            if c == '"':
                inq = False
        else:
            if c == '"':
                inq = True
            elif c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    return graph[i + 1:j]
        j += 1
    raise AssertionError(f"unterminated node body for {name!r}")


REASON_CLASSES = ("deliverable-gate", "infra-path", "llm-path", "unknown")


# ===========================================================================
# Behavior 1 — the failsafe block report carries failing node + reason class +
#              captured error context (629be1e0224f3a03).
# ===========================================================================

def test_emit_blk_report_carries_the_failing_node_identifier():
    """The failsafe report names the failing NODE — the workflow.fabro node at
    which the run failed — so the operator knows WHERE the run stopped, and it
    is an INTERPOLATED value read from the per-work run-context capture, NOT a
    static blank.

    RED (pre-fix): emit_blk emits only the content-free generic note with no
    failing-node field and reads no run context.
    """
    body = _node_body(_workflow_text(), "emit_blk")
    # The report reads a per-work run-context capture (making the current
    # note's hand-wavy "see run context" REAL, keyed by $WORK_ID like the
    # `arm` node's /tmp/fabro_pending_${WORK_ID}.txt precedent).
    assert "fabro_run_ctx_${WORK_ID}" in body, (
        "emit_blk must READ the per-work run-context capture "
        "(/tmp/fabro_run_ctx_${WORK_ID}) so the block report carries the actual "
        f"failing-node/context rather than a content-free string; body:\n{body}"
    )
    # The note carries a failing-node field whose value is INTERPOLATED from the
    # capture (a `$`-expanded variable), never an empty/static field.
    assert re.search(r"failing-node=\$", body), (
        "the block report must carry an INTERPOLATED failing-node field "
        "(failing-node=$...) sourced from the run-context capture, so the "
        f"operator sees WHERE the run stopped; body:\n{body}"
    )


def test_emit_blk_report_carries_a_reason_class_from_the_closed_set():
    """The failsafe report carries a REASON CLASS drawn from the closed set
    {deliverable-gate, infra-path, llm-path, unknown}, validated against that
    set (so an out-of-set value cannot leak through), and interpolated into the
    note.

    RED (pre-fix): emit_blk carries no reason class at all.
    """
    body = _node_body(_workflow_text(), "emit_blk")
    for token in REASON_CLASSES:
        assert token in body, (
            "emit_blk must constrain the reason class to the closed set "
            f"{REASON_CLASSES!r}; missing token {token!r}; body:\n{body}"
        )
    # The reason class is VALIDATED against the closed set (a `case` guard that
    # falls back to `unknown`), so a stray captured value cannot pass unchecked.
    assert re.search(r"case\b.*\$", body) and re.search(
        r"\|".join(re.escape(t) for t in REASON_CLASSES[:3]), body
    ), (
        "the reason class must be validated against the closed set (a case "
        f"guard over {REASON_CLASSES!r}); body:\n{body}"
    )
    assert re.search(r"reason-class=\$", body), (
        "the block report must carry an INTERPOLATED reason-class field "
        f"(reason-class=$...) drawn from the closed set; body:\n{body}"
    )


def test_emit_blk_report_carries_the_captured_error_context():
    """The failsafe report carries the captured error CONTEXT of the failing
    node — the run's failing output or tail — so the operator sees WHY it
    stopped, interpolated from the run-context capture.

    RED (pre-fix): emit_blk carries no captured context.
    """
    body = _node_body(_workflow_text(), "emit_blk")
    # The context is READ from the run-context capture ...
    assert "CONTEXT=" in body or "CONTEXT" in body, (
        "emit_blk must capture the error CONTEXT (the run's failing output / "
        f"tail) from the run-context capture; body:\n{body}"
    )
    # ... and interpolated into the note.
    assert re.search(r"context=\$", body), (
        "the block report must carry an INTERPOLATED context field "
        f"(context=$...) so the operator sees WHY the run stopped; body:\n{body}"
    )


def test_emit_blk_is_not_the_content_free_generic_summary():
    """The block report is NOT the generic content-free summary with an empty
    failing-node, empty reason, and empty body — the lead-01jw.3 regression this
    replaces.  The three diagnostic fields are all INTERPOLATED (proven above),
    so the note cannot be the static blank string.

    RED (pre-fix): the note is exactly the content-free generic string.
    """
    body = _node_body(_workflow_text(), "emit_blk")
    interpolated = [
        bool(re.search(rf"{field}=\$", body))
        for field in ("failing-node", "reason-class", "context")
    ]
    assert all(interpolated), (
        "the block report must carry ALL THREE interpolated diagnostic fields "
        "(failing-node, reason-class, context) sourced from run context, so it "
        "is never the content-free generic summary with empty node/reason/body; "
        f"interpolated={interpolated}; body:\n{body}"
    )


# ===========================================================================
# ADR-051 preserved — the enrichment must NOT regress the non-consuming report
# (these stay GREEN before and after; lead-i0wi F2 invariants).
# ===========================================================================

def test_emit_blk_stays_a_non_consuming_nudge_report():
    """ADR-051 / lead-i0wi F2: the enriched report is still a NON-consuming
    ``shop-msg nudge`` (dispatch stays pending == retriable), never a de-pending
    ``respond work_done --status blocked`` and never a work_done(complete).
    """
    body = _node_body(_workflow_text(), "emit_blk")
    assert "shop-msg nudge" in body, (
        f"emit_blk must still report via a non-consuming nudge; body:\n{body}"
    )
    assert "respond work_done" not in body and "--status blocked" not in body, (
        "the enrichment must NOT re-introduce the consuming `respond work_done "
        f"--status blocked` (ADR-051 / lead-i0wi F2); body:\n{body}"
    )
    assert "--status complete" not in body and "bc-emit" not in body, (
        "emit_blk must never emit a work_done(complete) — it is the fail-closed "
        f"block report (ADR-051: no false complete); body:\n{body}"
    )
    assert "--work-id" in body and "$WORK_ID" in body, (
        f"the non-consuming report must reference $WORK_ID; body:\n{body}"
    )


# ===========================================================================
# Block-only scenario-hash pin (same fidelity as lead-bnhn) — the pinned
# scenario text recomputes to its @scenario_hash tag.
# ===========================================================================

_FEATURE = (
    Path(__file__).resolve().parent.parent
    / "features"
    / "bc_container_fabro_failsafe_block_diagnostic.feature"
)
_BEHAVIOR_1_HASH = "629be1e0224f3a03"


def _scenario_blocks(text: str) -> dict[str, str]:
    lines = text.splitlines()
    blocks: dict[str, str] = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped_line = line.lstrip()
        m = (
            re.search(r"@scenario_hash:([0-9a-f]+)", line)
            if stripped_line.startswith("@")
            else None
        )
        if m:
            tag_hash = m.group(1)
            j = i + 1
            while j < len(lines) and lines[j].lstrip().startswith("@"):
                j += 1
            assert lines[j].lstrip().startswith("Scenario"), (
                f"Expected a Scenario line after the hash tag; got {lines[j]!r}"
            )
            start = j
            j += 1
            while j < len(lines):
                stripped = lines[j].lstrip()
                if stripped.startswith("@") or stripped.startswith("Scenario"):
                    break
                j += 1
            end = j
            while end > start + 1 and lines[end - 1].strip() == "":
                end -= 1
            blocks[tag_hash] = "\n".join(lines[start:end]) + "\n"
            i = j
            continue
        i += 1
    return blocks


@pytest.mark.skipif(
    shutil.which("scenarios") is None,
    reason="canonical `scenarios` CLI not on PATH",
)
def test_behavior_1_scenario_block_recomputes_to_its_pin():
    """The block-only hash of scenario 629be1e0224f3a03 recomputes to its tag.

    Teeth: any edit to the pinned scenario text not reflected in the tag (or a
    wrong/fabricated tag) makes the recompute diverge and REDs.
    """
    blocks = _scenario_blocks(_FEATURE.read_text(encoding="utf-8"))
    assert _BEHAVIOR_1_HASH in blocks, (
        f"No scenario tagged @scenario_hash:{_BEHAVIOR_1_HASH} in {_FEATURE.name}"
    )
    recomputed = subprocess.run(
        ["scenarios", "hash"],
        input=blocks[_BEHAVIOR_1_HASH],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert recomputed == _BEHAVIOR_1_HASH, (
        f"scenario block recomputed to {recomputed!r} but the feature pins "
        f"@scenario_hash:{_BEHAVIOR_1_HASH}; re-tag or revert the edit"
    )


# ===========================================================================
# Behavior 2 — the block report DERIVES the reason class and, for infra-path,
#              names the failing infra subsystem with a marker token, mirroring
#              the tmux-runtime launch-diagnostic cause-marker idiom
#              (738f35759127fe7f).
#
# Fidelity: these do NOT hard-code the fault->class lookup in the test.  They
# EXTRACT the shipped `classify_reason` derivation shell function VERBATIM from
# the REAL committed workflow.fabro `emit_blk` node and RUN it against each
# `<fault>` cell drawn from the pinned Scenario Outline's own Examples table,
# asserting it derives that row's `<reason_class>` and `<detail_marker>`.  The
# SPEC (the feature Examples) supplies the expected mapping; the SHIPPED code
# supplies the derivation; the test checks shipped-derivation == spec.  A test
# that merely echoed the table would not exercise the mechanism — this runs it.
# ===========================================================================

MARKER_TOKENS = (
    "deliverable",
    "oauth-shim",
    "agent-vault",
    "proxy",
    "rate-limit-429",
    "llm-path",
    "unknown",
)

_BEHAVIOR_2_HASH = "738f35759127fe7f"


def _classify_reason_fn(body: str) -> str:
    """Extract the shipped ``classify_reason() { ... esac; }`` derivation shell
    function VERBATIM from the emit_blk node body, unescaping the outer
    ``script="..."`` quote-escaping so it can be run as-is."""
    m = re.search(r"classify_reason\(\)\s*\{.*?esac\s*;\s*\}", body, re.DOTALL)
    assert m is not None, (
        "emit_blk must define a `classify_reason()` derivation function that "
        "classifies the failure into a reason class + infra-subsystem marker; "
        f"none found. body:\n{body}"
    )
    return m.group(0).replace('\\"', '"')


def _derive(body: str, node: str, context: str) -> tuple[str, str]:
    """Run the shipped ``classify_reason`` over (failing-node, captured-context)
    and return the derived ``(reason_class, detail_marker)``."""
    fn = _classify_reason_fn(body)
    script = fn + '\nclassify_reason "$1" "$2"\n'
    out = subprocess.run(
        ["sh", "-c", script, "sh", node, context],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    rc, _, dm = out.partition(" ")
    return rc, dm


def _examples_rows(scenario_hash: str) -> list[dict[str, str]]:
    """Parse the ``Examples:`` table of the Scenario Outline pinned by
    ``scenario_hash`` into a list of {column: value} dicts."""
    block = _scenario_blocks(_FEATURE.read_text(encoding="utf-8"))[scenario_hash]
    lines = block.splitlines()
    table: list[list[str]] = []
    in_examples = False
    for line in lines:
        s = line.strip()
        if s.startswith("Examples:"):
            in_examples = True
            continue
        if in_examples and s.startswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            table.append(cells)
    assert table, f"no Examples table for scenario {scenario_hash}"
    header, *data = table
    return [dict(zip(header, row)) for row in data]


def test_behavior_2_derives_reason_class_and_marker_for_every_examples_row():
    """The Scenario Outline bound to the REAL emit_blk mechanism: for EVERY
    ``<fault>`` row of the pinned Examples table, the shipped `classify_reason`
    derivation classifies the failure into exactly that row's ``<reason_class>``
    and names the failing subsystem/gate with that row's ``<detail_marker>`` —
    the tmux-runtime launch-diagnostic cause-marker idiom.

    RED (pre-fix): emit_blk has no derivation — `classify_reason` does not
    exist, so extraction fails and the outline cannot be exercised.
    """
    body = _node_body(_workflow_text(), "emit_blk")
    rows = _examples_rows(_BEHAVIOR_2_HASH)
    assert len(rows) == 7, f"expected the 7 pinned Examples rows; got {rows!r}"
    failures = []
    for row in rows:
        fault = row["fault"]
        want_rc = row["reason_class"]
        want_dm = row["detail_marker"]
        got_rc, got_dm = _derive(body, "", fault)
        if (got_rc, got_dm) != (want_rc, want_dm):
            failures.append(
                f"fault={fault!r} -> derived ({got_rc!r},{got_dm!r}) "
                f"but Examples pins ({want_rc!r},{want_dm!r})"
            )
    assert not failures, (
        "the shipped classify_reason derivation must map every Examples "
        "<fault> to its ({reason_class}, {detail_marker}); mismatches:\n"
        + "\n".join(failures)
    )


def test_behavior_2_reason_class_always_in_closed_set_and_marker_in_its_set():
    """The derivation NEVER emits an out-of-set reason class or marker: every
    Examples row's derived reason class is in the closed reason-class set and
    its derived marker is in the closed marker set (the closed-vocabulary
    guarantee the tmux cause-marker idiom also holds)."""
    body = _node_body(_workflow_text(), "emit_blk")
    for row in _examples_rows(_BEHAVIOR_2_HASH):
        rc, dm = _derive(body, "", row["fault"])
        assert rc in REASON_CLASSES, (
            f"derived reason class {rc!r} for fault {row['fault']!r} is not in "
            f"the closed set {REASON_CLASSES!r}"
        )
        assert dm in MARKER_TOKENS, (
            f"derived marker {dm!r} for fault {row['fault']!r} is not in the "
            f"closed marker set {MARKER_TOKENS!r}"
        )


def test_behavior_2_emit_blk_note_carries_interpolated_detail_marker():
    """The block report additionally names the failing subsystem/gate: the
    non-consuming nudge ``--note`` carries an INTERPOLATED ``detail-marker=$``
    field (the derived marker token), alongside — never replacing — the raw
    failing-node / reason-class / context diagnosis.

    RED (pre-fix): the note carries no detail-marker field.
    """
    body = _node_body(_workflow_text(), "emit_blk")
    assert re.search(r"detail-marker=\$", body), (
        "the block report must carry an INTERPOLATED detail-marker field "
        "(detail-marker=$...) naming the failing subsystem/gate, mirroring the "
        f"tmux launch-diagnostic cause-marker token; body:\n{body}"
    )
    # the raw diagnosis is still carried alongside (never replaced).
    for field in ("failing-node", "reason-class", "context"):
        assert re.search(rf"{field}=\$", body), (
            f"the classification must NOT replace the raw diagnosis: {field}=$ "
            f"must still be interpolated into the note; body:\n{body}"
        )


def test_behavior_2_marker_validated_against_its_closed_set():
    """The derived marker is VALIDATED against its closed set (a `case` guard
    that falls back to `unknown`), so a stray derived value cannot leak into the
    operator-facing marker — the same closed-vocabulary discipline the reason
    class already gets."""
    body = _node_body(_workflow_text(), "emit_blk")
    # the FULL closed marker set must appear as one contiguous case-guard
    # alternation (a partial/any-one match would give the test no teeth).
    guard = r"\|".join(re.escape(t) for t in MARKER_TOKENS)
    assert re.search(guard, body), (
        "the marker must be validated against its FULL closed set as one case "
        f"guard {MARKER_TOKENS!r} (stray -> unknown); body:\n{body}"
    )


@pytest.mark.skipif(
    shutil.which("scenarios") is None,
    reason="canonical `scenarios` CLI not on PATH",
)
def test_behavior_2_scenario_block_recomputes_to_its_pin():
    """The block-only hash of scenario 738f35759127fe7f recomputes to its tag,
    and behavior 1's pin (629be1e0224f3a03) is left undisturbed.

    Teeth: any edit to the pinned outline text (steps or Examples) not reflected
    in the tag makes the recompute diverge and REDs.
    """
    blocks = _scenario_blocks(_FEATURE.read_text(encoding="utf-8"))
    assert _BEHAVIOR_2_HASH in blocks, (
        f"No scenario tagged @scenario_hash:{_BEHAVIOR_2_HASH} in {_FEATURE.name}"
    )
    recomputed = subprocess.run(
        ["scenarios", "hash"],
        input=blocks[_BEHAVIOR_2_HASH],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert recomputed == _BEHAVIOR_2_HASH, (
        f"scenario block recomputed to {recomputed!r} but the feature pins "
        f"@scenario_hash:{_BEHAVIOR_2_HASH}; re-tag or revert the edit"
    )
    # behavior 1's pin stays undisturbed.
    b1 = subprocess.run(
        ["scenarios", "hash"],
        input=blocks[_BEHAVIOR_1_HASH],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert b1 == _BEHAVIOR_1_HASH, (
        f"behavior 1 pin disturbed: recomputed {b1!r} != {_BEHAVIOR_1_HASH}"
    )
