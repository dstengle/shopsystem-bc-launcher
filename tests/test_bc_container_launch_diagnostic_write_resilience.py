"""pytest-bdd binding for the launch-diagnostic write-resilience feature.

lead-bnhn (P1 bugfix): the launch-diagnostic write must be best-effort /
non-fatal (a write failure — e.g. an unwritable target dir — must NOT abort
the launch; a host-discoverable warning is surfaced and the launch
continues), and its DEFAULT target must be a user-writable per-user state dir
(NOT the root-owned /var/lib/bc-launcher). Additive tightening of robustness
properties scenario 56 (0d010cf8f3175226, 7084bbbfdef94f81) left unpinned.
"""
from pytest_bdd import scenarios

scenarios("../features/bc_container_launch_diagnostic_write_resilience.feature")
