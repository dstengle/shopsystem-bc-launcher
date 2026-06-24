"""pytest-bdd binding for the bc-base self-pin polled-dependency feature
(lead-dqje / lead-5yql).

The centralized scheduled poll (poll-bc-base-deps.yml) treats the
shopsystem-bc-launcher self-pin in docker/bc-base/Dockerfile as a 5th polled
dependency: it resolves bc-launcher's OWN latest release against its canonical
repo with the workflow's own GITHUB_TOKEN and bumps-then-commits-then-rebuilds
when stale, no-op when equal. These scenarios are ADDITIVE to the existing
four-dep family in bc_base_centralized_dep_poll.feature.
"""
from pytest_bdd import scenarios

scenarios("../features/bc_base_self_pin_poll.feature")
