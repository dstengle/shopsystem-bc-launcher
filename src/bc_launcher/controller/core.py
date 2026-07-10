"""Assembled BcContainerController.

Business logic for bc-container subcommands. All Docker interaction goes
through the DockerDriver interface, making this layer fully testable without a
live Docker daemon. The class body is split across mixins (launch,
provisioning, agent-session, commands); this module combines them into the
single public class and holds construction.
"""
from __future__ import annotations

import time

from bc_launcher.driver import DockerDriver, RegistryDriver
from bc_launcher.controller._launch import LaunchMixin
from bc_launcher.controller._provisioning import ProvisioningMixin
from bc_launcher.controller._agent_session import AgentSessionMixin
from bc_launcher.controller._engage import EngageMixin
from bc_launcher.controller._commands import CommandsMixin


class BcContainerController(
    LaunchMixin,
    ProvisioningMixin,
    AgentSessionMixin,
    EngageMixin,
    CommandsMixin,
):
    """Pure-Python controller for bc-container operations.

    Accepts a DockerDriver at construction time so tests can inject fakes.
    """
    """
    Pure-Python controller for bc-container operations.

    Accepts a DockerDriver at construction time so tests can inject fakes.
    """

    def __init__(
        self,
        driver: DockerDriver,
        registry_driver: RegistryDriver | None = None,
        monotonic=None,
    ) -> None:
        self._driver = driver
        # Injectable monotonic-clock seam (lead-cw7m).  The bounded
        # readiness-wait scan-dismiss loop budgets its TOTAL elapsed time
        # against this clock so the dismissal loop terminates at the 60s
        # readiness timeout rather than looping indefinitely.  Production
        # passes nothing (time.monotonic); tests inject a deterministic clock
        # to drive the bounded-timeout path without real wall-clock waits.
        self._monotonic = monotonic if monotonic is not None else time.monotonic
        # Optional registry seam (scenario af2f03d3ac519cb5).  When present,
        # launch resolves the bc-base "latest" tag's current registry digest
        # BEFORE starting the container, and runs the container from that
        # resolved digest rather than whatever digest the local cache holds
        # under "latest".  Absent (the default), launch runs from BC_IMAGE as
        # before — the resolution step is purely additive.
        self._registry_driver = registry_driver
