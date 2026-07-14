"""pytest-bdd binding for the tmux-DEFAULT autonomous engage restoration
(lead-ew86, @scenario_hash:e811193fc061e1e8 — ADR-050 D3 / ADR-018 D1-D2).

The ADR-050 --orchestrator split regressed the tmux DEFAULT engage to
arm-watcher+drain-then-"await user direction". This pins the RESTORED
autonomous default: the default startup prompt the launcher injects on the
tmux-default path DIRECTS drain-AND-process of each pending dispatch through
the Implementer->Reviewer loop to a Reviewer-gated work_done, with no
human-injected "go" between the drain and the work_done.

FIDELITY: the step defs (tests/steps/tmux_default_autonomous_engage.py) drive
the REAL launcher (controller.launch over the FakeDockerDriver, resolving the
default startup prompt exactly as bc_launcher.cli.main() does when no
--startup-prompt is supplied) and bind to its ACTUAL recorded tmux `agent`
send-keys — never to a model.
"""
from pytest_bdd import scenarios

scenarios("../features/bc_container_tmux_default_autonomous_engage.feature")
