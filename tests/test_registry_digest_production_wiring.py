"""Production-wiring tests for the launch digest-resolution path (lead-l5a2h).

These tests exist because the pytest-bdd binding for
@scenario_hash:af2f03d3ac519cb5 passed while production was broken: it injects
a FakeRegistryDriver, so it exercised the controller seam and never the real
launch path's construction site.  bd shopsystem_bc_launcher-7pmt.

Everything here targets the REAL wiring:

* ``build_controller()`` is the real launch path's construction site, and the
  controller it returns must carry a real registry driver (7pmt.1).
* ``RealRegistryDriver.resolve_digest`` must read the MANIFEST digest -- the
  one a ``repo@sha256:...`` pull resolves -- not the config digest, and not via
  the buildx plugin bc-base does not ship (7pmt.2).
* Resolution failure must be loud, not a silent fallback to the bare tag
  (7pmt.3).
"""
from __future__ import annotations

import pytest

from bc_launcher import cli
from bc_launcher.driver import RealDockerDriver, RealRegistryDriver


def test_real_launch_path_controller_carries_a_real_registry_driver():
    """The real launch path must construct a controller with BOTH real drivers.

    Fault 1: cli.py built ``BcContainerController(RealDockerDriver())`` with no
    registry_driver, so controller/_launch_prep.py's digest-resolution + pull
    block was unconditionally skipped on every real launch.
    """
    controller = cli.build_controller()

    assert isinstance(controller._driver, RealDockerDriver)
    assert isinstance(controller._registry_driver, RealRegistryDriver), (
        "the real launch path must inject a RealRegistryDriver; without it "
        "_launch_prep.py skips digest resolution entirely and launch runs "
        "whatever stale digest the local cache holds under 'latest'"
    )


def test_main_launch_obtains_its_controller_from_the_wired_construction_site(
    monkeypatch,
):
    """main()'s launch path must go through build_controller(), not build its own.

    Guards the exact regression shape of fault 1: a correctly-wired factory
    that main() bypasses would re-break production while this module's other
    tests stayed green.
    """

    class _FactoryReached(Exception):
        pass

    def _spy():
        raise _FactoryReached

    monkeypatch.setattr(cli, "build_controller", _spy)

    with pytest.raises(_FactoryReached):
        cli.main(
            ["launch", "shopsystem-messaging", "--repo-url", "https://example.invalid/x.git"]
        )
