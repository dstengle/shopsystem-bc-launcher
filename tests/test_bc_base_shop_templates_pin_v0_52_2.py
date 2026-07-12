"""The bc-base Dockerfile pins shop-templates at v0.52.2 (lead-a3kg-2 release).

The v0.3.63 re-release (lead-a3kg-2) re-pins the bc-base image's shop-templates
VCS pin to v0.52.2 so the rebuilt ghcr bc-base:latest bakes the keystone
shop-templates release that pours the FULL .fabro def INCLUDING the dispatcher
def (dispatcher.toml / dispatcher.fabro / dispatch_acp_agent.py — the lead-5qj1
dispatcher-def pour fix). This test asserts the concrete pinned version (not
merely the vMAJOR.MINOR.PATCH shape that tests/test_bc_base_shop_templates_pin.py
already covers).
"""
from tests.support.base_image import _baked_shop_templates_version

EXPECTED_SHOP_TEMPLATES_PIN = "v0.52.2"


def test_bc_base_dockerfile_pins_shop_templates_v0_52_2():
    pin = _baked_shop_templates_version()
    assert pin == EXPECTED_SHOP_TEMPLATES_PIN, (
        "bc-base Dockerfile ARG SHOP_TEMPLATES_VERSION must be "
        f"{EXPECTED_SHOP_TEMPLATES_PIN} so the rebuilt bc-base:latest bakes "
        "the keystone shop-templates release that pours the full .fabro def "
        f"(including the dispatcher def) at launch; got {pin!r}."
    )
