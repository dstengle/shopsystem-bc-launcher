"""Defect C (work_id lead-ifye3.14): @scenario_hash:5d49031bab379ba6's Given B
must be SELF-CONTAINED — it must not depend on a launch that has not happened.

THE DEFECT.  Given B ("the shopsystem-bc-launcher dispatcher's per-child
"fabro run --provider" construction passes the REGISTERED fabro provider
identity ...") is the capstone's SIXTH Given.  Its step def read the recorded
engage exec out of `ctx["cadr_driver"]` — a key populated ONLY by an actual
launch (tests/support/container.py, `_odd9_drive_fabro_launch`), which is driven
by the "When bc-container launch is run ..." step.  A When runs AFTER every
Given, so `ctx["cadr_driver"]` is unconditionally absent when Given B executes:
the Given raised `KeyError: 'cadr_driver'` every time it was reached.

The bug was MASKED only because Given A (the shop-templates model_stylesheet
pour, Defect A / lead-ifye3.6) skipped first on this container's stale installed
`shop_templates`.  The moment that pour refreshes, the capstone flips from an
honest SKIP to an opaque KeyError — an infra failure standing in for what should
be a real end-to-end run.  Reproduced under probe by lead-ifye3.12's reviewer,
lead-ifye3.13's implementer, and again here.

WHY SELF-CONTAINED IS THE FAITHFUL SHAPE (not merely the convenient one).  The
scenario text pins this precondition as "satisfied once, prior to and
independent of this launch".  A check that can only run AFTER the launch would
contradict that clause.  Given A — the sibling precondition — already binds
directly to its real artifact (the poured `templates/fabro/workflow.fabro`) with
no launch dependency; Given B now matches that shape, binding to the REAL
call-site construction (`_fabro_engage_script`, the pure function engage.py's
launch path itself calls) rather than to a launch's recorded side effects.  This
is still bound to the launcher's ACTUAL construction — never a string match on
source, and never a model.

Scenario text at 5d49031bab379ba6 is NOT altered by this fix: step-def only.
"""
from tests.steps.llm_provider import (
    dispatcher_passes_registered_provider_identity,
)


def test_given_b_provider_identity_check_needs_no_prior_launch(tmp_path):
    """Given B's precondition check must evaluate with NO launch having run —
    i.e. with no `cadr_driver` in ctx, exactly as it stands as the capstone's
    6th Given, before the "When bc-container launch is run" step executes.

    RED (pre-fix): raises KeyError: 'cadr_driver'.
    GREEN: the self-contained check reads the REAL call-site construction and,
    with Defect B landed (lead-ifye3.13 — the dispatcher passes the REGISTERED
    identity "openai"), marks the precondition satisfied.
    """
    # No launch has occurred: this is the ctx state Given B genuinely runs in.
    ctx = {}

    dispatcher_passes_registered_provider_identity(
        run_flag="fabro run --provider",
        registered="openai",
        active="openrouter",
        ctx=ctx,
        tmp_path=tmp_path,
    )

    # Defect B IS landed on this base, so the precondition is genuinely
    # satisfied — the Given must PASS, not skip, and must not have needed a
    # launch to say so.
    assert ctx.get("b6_provider_identity_precondition_satisfied") is True, (
        "Given B must mark the provider-identity precondition satisfied from "
        "the launcher's real call-site construction alone, with no launch"
    )
