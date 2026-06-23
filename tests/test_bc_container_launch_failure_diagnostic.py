"""pytest-bdd binding for the launch-failure persisted-diagnostic feature.

lead-63em (re-issue of lead-2qta): a launch that fails to bring up a usable
agent session persists a diagnostic FILE — carrying the literal cause-marker
token — to a documented per-BC host-discoverable location, readable from the
host without a tmux attach and independent of the (ephemeral) launch stderr.
"""
from pytest_bdd import scenarios

scenarios("../features/bc_container_launch_failure_diagnostic.feature")
