"""pytest-bdd binding for the bd-bootstrap resilience bugfix (lead-5k8c).

Pins two additive behaviors on the in-container bd-bootstrap step:
  1. EMPTY-REMOTE PROVISIONING — an empty `<bc>-beads` Dolt remote is
     INITIALIZED (init-and-push an initial branch/commit) then provisioned
     write-ready, instead of fatal-failing the clone.
  2. NO PRE-AGENT-START STEP MAY FATAL-STRAND — any bd-bootstrap failure
     (including a seed that could not initialize the remote) WARNS and
     proceeds to agent-start (generalizes the lead-k4k7 invariant).
"""
from pytest_bdd import scenarios

scenarios("../features/bc_container_beads_bootstrap_resilience.feature")
