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

import os
import re
import shutil
import stat
import subprocess
import tempfile
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


# ===========================================================================
# Behavior 3 — CROSS-RUNTIME PARITY (8af4e27a05ae9a32).
#
# The fabro finite-run blocked report is as ACTIONABLE as an equivalent
# tmux-runtime launch diagnostic: both runtimes expose the SAME three operator
# decision inputs — the failing point (WHERE), the failure class (WHAT class,
# for routing), and the captured context (WHY) — so an operator reconciling a
# work_id and deciding how to route the failure (retry / escalate infra /
# escalate LLM path / return the deliverable gate to the PO/Architect) reaches
# the SAME decision from either runtime's response, with no need to attach into
# the container or read fabro run logs out of band.
#
# PIN-EXISTING-CAPABILITY: behaviors 1&2 already put {failing-node, reason-class,
# detail-marker, context} on the fabro `emit_blk` note, and the tmux runtime
# already exposes its launch diagnostic (cause-marker token + human-readable
# reason) via `bc_launcher.diagnostics` / the `_write_launch_diagnostic`
# content format.  These tests do NO hard-coded tautology: they bind BOTH
# runtimes to their REAL mechanisms and assert the two expose the same three
# decision inputs AND share the infra failure vocabulary (the crux: the token
# an operator routes on — e.g. `agent-vault` — is the SAME token in both
# runtimes, so the shipped tmux `CAUSE_MARKER_AGENT_VAULT` constant must equal
# what the shipped fabro `classify_reason` derives for the same failure).
# ===========================================================================

_BEHAVIOR_3_HASH = "8af4e27a05ae9a32"


def _engage_source() -> str:
    """The REAL committed `_write_launch_diagnostic` call site — the tmux-runtime
    launch-diagnostic content format (`cause:` + `reason:` fields)."""
    return (
        Path(__file__).resolve().parent.parent
        / "src"
        / "bc_launcher"
        / "controller"
        / "_engage.py"
    ).read_text(encoding="utf-8")


def test_behavior_3_fabro_block_exposes_the_three_decision_inputs():
    """The fabro blocked report exposes all THREE operator decision inputs the
    parity requires — the failing point (failing-node=WHERE), the failure class
    (reason-class=WHAT class), and the captured context (context=WHY) — each an
    INTERPOLATED field on the real emit_blk nudge note (behaviors 1&2), so the
    fabro response is self-contained and actionable.
    """
    body = _node_body(_workflow_text(), "emit_blk")
    decision_inputs = {
        "failing point": r"failing-node=\$",
        "failure class": r"reason-class=\$",
        "captured context": r"context=\$",
    }
    missing = [name for name, pat in decision_inputs.items() if not re.search(pat, body)]
    assert not missing, (
        "the fabro blocked report must expose all three operator decision "
        f"inputs on its note; missing {missing!r}; body:\n{body}"
    )


def test_behavior_3_tmux_launch_diagnostic_exposes_the_same_three_decision_inputs():
    """The tmux-runtime launch diagnostic exposes the SAME three operator
    decision inputs, bound to the REAL mechanism: the closed `CAUSE_MARKER_*`
    vocabulary names the failing point / failure class (WHERE + WHAT class),
    and the persisted-diagnostic content format carries a `cause:` field (that
    marker) plus a `reason:` field (the captured context / WHY).

    This is not a tautology: it reads the shipped diagnostics constants and the
    shipped `_write_launch_diagnostic` content format; drop either field or the
    marker vocabulary and this REDs.
    """
    from bc_launcher import diagnostics

    markers = [
        getattr(diagnostics, n) for n in dir(diagnostics)
        if n.startswith("CAUSE_MARKER_")
    ]
    assert markers, (
        "the tmux runtime must define a closed CAUSE_MARKER_* vocabulary naming "
        "the failing point / failure class"
    )
    src = _engage_source()
    # failing point / failure class field (the cause-marker token) ...
    assert re.search(r'cause:\s*\{cause_marker\}', src), (
        "the tmux launch diagnostic must carry a `cause:` field (the "
        f"cause-marker token = failing point / failure class); source lacks it"
    )
    # ... and the captured-context field (the human-readable reason = WHY).
    assert re.search(r'reason:\s*\{reason\}', src), (
        "the tmux launch diagnostic must carry a `reason:` field (the captured "
        f"context / WHY the session failed); source lacks it"
    )


def test_behavior_3_infra_vocabulary_is_shared_so_operator_routes_identically():
    """CRUX of the parity: the token an operator ROUTES on for an infra failure
    is the SAME token in both runtimes.  For an agent-vault infra failure the
    shipped fabro `classify_reason` derives `(infra-path, agent-vault)`, and the
    shipped tmux `CAUSE_MARKER_AGENT_VAULT` is exactly `agent-vault` — so the
    operator reading either runtime's response sees the same `agent-vault`
    token and reaches the SAME route ("escalate infra"), with no runtime-attach.

    Real teeth, no hard-coded tautology: rename either runtime's token and the
    equality REDs; it runs the REAL fabro derivation and reads the REAL tmux
    constant.
    """
    from bc_launcher.diagnostics import CAUSE_MARKER_AGENT_VAULT

    body = _node_body(_workflow_text(), "emit_blk")
    rc, dm = _derive(body, "", "the agent-vault broker the container routes through was unreachable")
    assert rc == "infra-path", (
        f"the fabro runtime must class an agent-vault failure as infra-path; got {rc!r}"
    )
    assert dm == CAUSE_MARKER_AGENT_VAULT == "agent-vault", (
        "the infra token the operator routes on must be IDENTICAL across "
        f"runtimes: fabro derived detail-marker {dm!r} vs tmux "
        f"CAUSE_MARKER_AGENT_VAULT {CAUSE_MARKER_AGENT_VAULT!r}"
    )


def test_behavior_3_fabro_reason_class_covers_the_four_scenario_routes():
    """The operator can reconcile the work_id and route the failure using ONLY
    the fabro blocked work_done: the closed reason-class set the fabro report
    carries maps onto the four routes the scenario names — retry (unknown),
    escalate infra (infra-path), escalate LLM path (llm-path), and return the
    deliverable gate to the PO/Architect (deliverable-gate) — so every route is
    reachable from the fabro response alone.
    """
    body = _node_body(_workflow_text(), "emit_blk")
    # every reason class in the closed set is reachable via the shipped
    # derivation over a representative fault (drawn from behavior 2's spec).
    route_faults = {
        "deliverable-gate": "a deliverable Reviewer gate rejected the produced work",
        "infra-path": "the agent-vault broker was unreachable",
        "llm-path": "the LLM produced an unusable or non-advancing response",
        "unknown": "the run failed for a cause the failsafe could not classify",
    }
    for want_rc, fault in route_faults.items():
        got_rc, _ = _derive(body, "", fault)
        assert got_rc == want_rc, (
            f"route {want_rc!r} not reachable from the fabro report: fault "
            f"{fault!r} derived reason class {got_rc!r}"
        )
    assert set(route_faults) == set(REASON_CLASSES), (
        "the four scenario routes must correspond exactly to the closed "
        f"reason-class set {REASON_CLASSES!r}"
    )


@pytest.mark.skipif(
    shutil.which("scenarios") is None,
    reason="canonical `scenarios` CLI not on PATH",
)
def test_behavior_3_scenario_block_recomputes_to_its_pin():
    """The block-only hash of scenario 8af4e27a05ae9a32 recomputes to its tag,
    and behaviors 1 & 2's pins (629be1e0224f3a03 / 738f35759127fe7f) are left
    undisturbed.

    RED (pre-bind): the parity scenario is not yet appended to the feature file,
    so it is not pinned/bound here and this REDs.  Teeth thereafter: any edit to
    the pinned parity text not reflected in the tag diverges and REDs.
    """
    blocks = _scenario_blocks(_FEATURE.read_text(encoding="utf-8"))
    assert _BEHAVIOR_3_HASH in blocks, (
        f"No scenario tagged @scenario_hash:{_BEHAVIOR_3_HASH} in {_FEATURE.name}"
    )
    recomputed = subprocess.run(
        ["scenarios", "hash"],
        input=blocks[_BEHAVIOR_3_HASH],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert recomputed == _BEHAVIOR_3_HASH, (
        f"scenario block recomputed to {recomputed!r} but the feature pins "
        f"@scenario_hash:{_BEHAVIOR_3_HASH}; re-tag or revert the edit"
    )
    # behaviors 1 & 2 pins stay undisturbed.
    for h in (_BEHAVIOR_1_HASH, _BEHAVIOR_2_HASH):
        got = subprocess.run(
            ["scenarios", "hash"],
            input=blocks[h],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert got == h, f"pin {h} disturbed: recomputed {got!r}"


# ===========================================================================
# Behavior 4 — the LAST-RESORT FAILSAFE FLOOR (b5bd016991cc2774).
#
# This pins the FLOOR of the emit_blk failsafe: the deepest last-resort path,
# where the failure is UNCLASSIFIABLE and/or the per-work run-context capture is
# degenerate (missing, or truncated to blanks).  Even there the block report
# must stay diagnosable rather than content-free:
#   (1) reason class is set to `unknown` (never left empty) WITH the failing
#       node identifier and the captured run tail still attached;
#   (2) the failsafe emits status BLOCKED — never a silent `complete` for a run
#       with no deliverable (no `--status complete`, no `bc-emit`, fails closed
#       to the non-consuming blocked report);
#   (3) the report is NEVER the bare content-free block with an empty node /
#       empty reason / empty body — closing the lead-01jw.3 regression at the
#       floor.
#
# Fidelity: these do NOT regex-inspect the script body (behaviors 1-3 do that).
# They EXECUTE the REAL shipped emit_blk `script=` end-to-end under `sh`, with a
# stub `shop-msg` on PATH capturing the ACTUAL emitted invocation, and assert on
# the concrete note the operator would receive at the floor.  A blank/whitespace
# failing-node or context that slips past a naive `-n` guard is caught here — a
# static regex could not see it.
# ===========================================================================

_BEHAVIOR_4_HASH = "b5bd016991cc2774"

# the old lead-01jw.3 content-free generic summary this floor must never regress
# to (the empty-node/empty-reason/empty-body block).
_CONTENT_FREE_GENERIC = (
    "a deliverable-side gate or step failed (see run context); "
    "reporting blocked, never a silent complete"
)


def _emit_blk_script() -> str:
    """The REAL shipped emit_blk ``script="..."`` body, unescaped so it runs as
    the container would run it."""
    body = _node_body(_workflow_text(), "emit_blk")
    m = re.search(r'script="(.*)"\s*$', body.strip(), re.DOTALL)
    assert m is not None, f"emit_blk node has no script= attribute; body:\n{body}"
    return m.group(1).replace('\\"', '"')


def _run_emit_blk(work_id: str, ctx_content: str | None) -> tuple[list[str], str]:
    """EXECUTE the real emit_blk script for ``work_id`` against a run-context
    capture of ``ctx_content`` (``None`` == the capture file is absent — the true
    last-resort floor).  A stub ``shop-msg`` on PATH records the ACTUAL emitted
    argv; returns ``(argv, note)`` where ``note`` is the ``--note`` value the
    operator would receive.
    """
    script = _emit_blk_script()
    ctxf = Path(f"/tmp/fabro_run_ctx_{work_id}")
    with tempfile.TemporaryDirectory() as d:
        cap = Path(d) / "capture"
        stub = Path(d) / "shop-msg"
        stub.write_text(f'#!/bin/sh\nprintf "%s\\0" "$@" > {cap}\n')
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        try:
            if ctx_content is None:
                ctxf.unlink(missing_ok=True)
            else:
                ctxf.write_text(ctx_content)
            env = dict(os.environ)
            env["PATH"] = f"{d}:{env['PATH']}"
            env["WORK_ID"] = work_id
            env["BC_NAME"] = "bc-shopsystem-messaging"
            subprocess.run(["sh", "-c", script], env=env, check=True)
            raw = cap.read_bytes()
        finally:
            ctxf.unlink(missing_ok=True)
    argv = [a.decode() for a in raw.split(b"\0")[:-1]]
    assert "--note" in argv, f"emit_blk stub captured no --note; argv={argv!r}"
    note = argv[argv.index("--note") + 1]
    return argv, note


def _note_fields(note: str) -> dict[str, str]:
    """Parse the ``failing-node=.. reason-class=.. detail-marker=.. context=..``
    tail of the emitted note into its field values (context runs to end)."""
    fields: dict[str, str] = {}
    for key, nxt in (
        ("failing-node", "reason-class"),
        ("reason-class", "detail-marker"),
        ("detail-marker", "context"),
    ):
        m = re.search(rf"{key}=(.*?) {nxt}=", note)
        assert m is not None, f"note missing {key}= field; note={note!r}"
        fields[key] = m.group(1)
    m = re.search(r"context=(.*)$", note)
    assert m is not None, f"note missing context= field; note={note!r}"
    fields["context"] = m.group(1)
    return fields


def test_behavior_4_floor_degenerate_capture_never_emits_a_blank_node_or_context():
    """FLOOR guarantee (1)+(3): when the run-context capture is DEGENERATE —
    truncated to whitespace-only NODE/CONTEXT (a real last-resort possibility: a
    partial/interrupted capture) — the emitted block report must STILL carry a
    non-blank failing-node and a non-blank context, never a content-free
    ``failing-node=`` / ``context=`` blank.

    RED (pre-fix): emit_blk guards the fields with ``[ -n "$FN" ]`` /
    ``[ -n "$CTX" ]``, which a whitespace-only value PASSES, so the floor emits
    ``failing-node=<spaces>`` and ``context=<spaces>`` — a content-free block.
    """
    _argv, note = _run_emit_blk(
        "b4_degenerate", "NODE=   \nCONTEXT=   \nCONTEXT=   \n"
    )
    fields = _note_fields(note)
    assert fields["failing-node"].strip() != "", (
        "at the floor, a whitespace-only captured NODE must fall back to a "
        "non-blank identifier (e.g. `unknown`), never a content-free "
        f"`failing-node=<blank>`; note={note!r}"
    )
    assert fields["context"].strip() != "", (
        "at the floor, a whitespace-only captured CONTEXT must fall back to a "
        "non-blank run-tail placeholder, never a content-free "
        f"`context=<blank>`; note={note!r}"
    )
    # and the reason class is still the closed-set `unknown`, not empty.
    assert fields["reason-class"].strip() == "unknown", (
        f"unclassifiable floor must set reason-class=unknown; note={note!r}"
    )


def test_behavior_4_floor_unclassifiable_emits_unknown_with_the_run_tail():
    """FLOOR guarantee (1): for an UNCLASSIFIABLE failure that DID capture a run
    tail, the emitted report sets reason class to ``unknown`` (not empty) AND
    still attaches the failing node identifier and the captured run tail, so even
    the unclassified case is diagnosable.
    """
    _argv, note = _run_emit_blk(
        "b4_unclassifiable",
        "NODE=implement_step\n"
        "CONTEXT=some wholly unexpected boom\n"
        "CONTEXT=zzz qqq nothing recognizable\n",
    )
    fields = _note_fields(note)
    assert fields["reason-class"] == "unknown", (
        f"an unclassifiable failure must class as unknown; note={note!r}"
    )
    assert fields["failing-node"] == "implement_step", (
        f"the failing node identifier must still be attached; note={note!r}"
    )
    assert "unexpected boom" in fields["context"], (
        "the captured run tail must still be attached alongside the unknown "
        f"reason class; note={note!r}"
    )


def test_behavior_4_floor_missing_capture_is_still_diagnosable_not_content_free():
    """FLOOR guarantee (1)+(3): the TRUE last-resort — no run-context capture
    file at all — still yields a diagnosable report: reason class ``unknown``,
    a non-blank failing-node, and a non-blank context placeholder, never the
    bare content-free block with empty node/reason/body.
    """
    _argv, note = _run_emit_blk("b4_missing", None)
    fields = _note_fields(note)
    assert fields["reason-class"] == "unknown"
    assert fields["failing-node"].strip() != ""
    assert fields["context"].strip() != ""
    # never the old content-free generic summary.
    assert _CONTENT_FREE_GENERIC not in note, (
        "the floor must never regress to the content-free generic summary; "
        f"note={note!r}"
    )


def test_behavior_4_floor_emits_blocked_never_a_silent_complete():
    """FLOOR guarantee (2): the emitted invocation is the NON-consuming blocked
    report (``shop-msg nudge`` … ``--reason stuck-on-you``) — it NEVER emits a
    ``--status complete`` or a ``bc-emit`` complete for a run that produced no
    deliverable.  Asserted on the ACTUAL captured argv, not just the body.
    """
    argv, note = _run_emit_blk("b4_blocked", "NODE=x\nCONTEXT=boom\n")
    assert argv[:1] == ["nudge"], (
        f"the floor must report via `shop-msg nudge`; captured argv={argv!r}"
    )
    assert "--status" not in argv and "complete" not in argv, (
        f"the floor must never emit a status/complete work_done; argv={argv!r}"
    )
    assert "bc-emit" not in argv, (
        f"the floor must never route through bc-emit; argv={argv!r}"
    )
    assert "BLOCKED" in note, (
        f"the floor report must announce it is BLOCKED; note={note!r}"
    )
    # body-level fail-closed invariant (ADR-051 / lead-i0wi F2) at the floor.
    body = _node_body(_workflow_text(), "emit_blk")
    assert "--status complete" not in body and "bc-emit" not in body, (
        f"emit_blk must contain no complete path; body:\n{body}"
    )


@pytest.mark.skipif(
    shutil.which("scenarios") is None,
    reason="canonical `scenarios` CLI not on PATH",
)
def test_behavior_4_scenario_block_recomputes_to_its_pin():
    """The block-only hash of scenario b5bd016991cc2774 recomputes to its tag,
    and behaviors 1-3's pins are left undisturbed.

    RED (pre-bind): the floor scenario is not yet appended to the feature file,
    so it is not pinned/bound here and this REDs.  Teeth thereafter: any edit to
    the pinned floor text not reflected in the tag diverges and REDs.
    """
    blocks = _scenario_blocks(_FEATURE.read_text(encoding="utf-8"))
    assert _BEHAVIOR_4_HASH in blocks, (
        f"No scenario tagged @scenario_hash:{_BEHAVIOR_4_HASH} in {_FEATURE.name}"
    )
    recomputed = subprocess.run(
        ["scenarios", "hash"],
        input=blocks[_BEHAVIOR_4_HASH],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert recomputed == _BEHAVIOR_4_HASH, (
        f"scenario block recomputed to {recomputed!r} but the feature pins "
        f"@scenario_hash:{_BEHAVIOR_4_HASH}; re-tag or revert the edit"
    )
    # behaviors 1-3 pins stay undisturbed.
    for h in (_BEHAVIOR_1_HASH, _BEHAVIOR_2_HASH, _BEHAVIOR_3_HASH):
        got = subprocess.run(
            ["scenarios", "hash"],
            input=blocks[h],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert got == h, f"pin {h} disturbed: recomputed {got!r}"
