"""pytest-bdd binding for the fabro-def VALIDITY pin (lead-ky63,
@scenario_hash:2dfefe2ba81e418d).

Companion block-only scenario PIN for the self-contained fabro loop def
bundle delivered by lead-h2bj under ``src/bc_launcher/assets/fabro-def/``.
Where lead-h2bj's plain unit tests
(``tests/test_bc_container_fabro_def_bundle.py``) guard the DELIVERY, this
scenario pins the def's VALIDITY as an ADR-051 Implementer->Reviewer loop.

FIDELITY (run the REAL tool, do not reimplement):
* LEG 1 runs the REAL fabro binary (fabro-sh/fabro v0.254.0, target-triple
  release asset per bead 0fz) `validate` against the committed def's
  workflow.fabro and asserts exit 0 + an EMPTY diagnostics array. SKIPs
  honestly only if the binary genuinely cannot be obtained (no network); a
  real failure is a real def defect and REDs.
* LEG 2 parses the REAL committed workflow.fabro (quote-aware,
  comment-stripped) and asserts the ADR-051 structural invariants with teeth:
  graph present; every prompt_file node body present; `emit_r` (reviewer) the
  SOLE scenario-path gated work_done(complete) emitter; every fallible
  non-terminal node has an unconditional failsafe edge to halt/emit_blk.
* LEG 3 asserts the native vault (vaults/default/secrets.json) is
  __PLACEHOLDER__-only, valid JSON, no real-credential-shaped literal
  (ADR-049).

Step definitions live in tests/conftest.py (lead-ky63 block).
"""
from pytest_bdd import scenarios

scenarios("../features/bc_container_fabro_def_validates.feature")
