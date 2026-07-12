"""The bc-base Dockerfile pins shopsystem-messaging at v0.4.6 and scenarios at
v0.3.1 (lead-14xb.2 recovery re-release).

The v0.3.61 release recovers the FAILED lead-8z06 bc-base publish
(publish-bc-base.yml ResolutionImpossible: shop-templates 0.52.0 requires
scenarios 0.3.1, but messaging v0.4.4 pinned scenarios 0.2.0). messaging v0.4.6
is now released with scenarios pinned 0.3.1 (ADR-060), so co-installing
shop-templates 0.52.0 + messaging v0.4.6 + scenarios 0.3.1 resolves. This test
asserts the concrete pinned versions (not merely the vMAJOR.MINOR.PATCH shape
that tests/steps/base_image.py already covers by shape) so a regression to the
conflicting v0.4.4 / v0.2.0 pins RED-tests.
"""
import re

from tests.support.base_image import _bc_base_dockerfile_text

EXPECTED_MESSAGING_PIN = "v0.4.6"
EXPECTED_SCENARIOS_PIN = "v0.3.1"


def _dockerfile_vcs_pin(text: str, package: str, repo: str) -> str | None:
    """Return the vX.Y.Z tag the bc-base Dockerfile pins <package> to from the
    dstengle/<repo> VCS requirement, or None if the pin is absent."""
    m = re.search(
        rf"{re.escape(package)} @ git\+https://github\.com/dstengle/"
        rf"{re.escape(repo)}(?:\.git)?@(v\d+\.\d+\.\d+)",
        text,
    )
    return m.group(1) if m else None


def test_bc_base_dockerfile_pins_messaging_v0_4_6():
    pin = _dockerfile_vcs_pin(
        _bc_base_dockerfile_text(), "shopsystem-messaging", "shopsystem-messaging"
    )
    assert pin == EXPECTED_MESSAGING_PIN, (
        "bc-base Dockerfile must pin shopsystem-messaging at "
        f"{EXPECTED_MESSAGING_PIN} (which pins scenarios 0.3.1 per ADR-060) so "
        "the rebuilt bc-base:latest co-installs shop-templates 0.52.0 + "
        "messaging v0.4.6 + scenarios 0.3.1 without ResolutionImpossible; "
        f"got {pin!r}."
    )


def test_bc_base_dockerfile_pins_scenarios_v0_3_1():
    pin = _dockerfile_vcs_pin(
        _bc_base_dockerfile_text(), "scenarios", "shopsystem-scenarios"
    )
    assert pin == EXPECTED_SCENARIOS_PIN, (
        "bc-base Dockerfile must pin scenarios at "
        f"{EXPECTED_SCENARIOS_PIN} so it agrees with shop-templates 0.52.0's "
        "scenarios 0.3.1 requirement (the lead-8z06 ResolutionImpossible was a "
        "scenarios 0.2.0 vs 0.3.1 conflict); got {pin!r}.".format(pin=pin)
    )
