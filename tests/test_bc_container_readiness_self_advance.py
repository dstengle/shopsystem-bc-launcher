"""
pytest-bdd test module for the readiness-wait self-advance handling
(lead-gw9v / lead-c713 — the SECOND lead-c713 readiness bug, DISTINCT from
lead-cw7m's step-4 prompt-escape).

Binds features/bc_container_readiness_self_advance.feature (scenarios
e30b15363815abed / f3784811e04a224d / 9fa36102d756a8fb).

The defect: the prior readiness sequence hard-gated on the PRE-trust banner
"Accessing workspace:" and ABORTED with an "agent-startup failure" the instant
that transient banner was not caught by polling.  bc-base bakes
`bypassPermissionsModeAccepted`, so claude SELF-ADVANCES past the
workspace-trust prompt straight to the input-ready marker "bypass permissions
on"; the transient banner is then never caught, so the hard gate dropped every
self-advancing unattended launch even though claude was healthy and at
input-ready.

The fix restructures step-2/step-3/step-4 into a coherent bounded readiness
loop that polls for EITHER the banner (→ accept trust with Enter → input-ready
→ inject) OR the already-present input-ready marker (→ treat as up, SKIP the
trust-accept Enter, inject), aborting non-zero only if NEITHER is reached
within the readiness timeout.  It COMPOSES with — and does NOT supersede —
lead-cw7m's step-4 auto-dismiss + trust handling.

Step definitions live in conftest.py.
"""
from pytest_bdd import scenarios

scenarios("../features/bc_container_readiness_self_advance.feature")
