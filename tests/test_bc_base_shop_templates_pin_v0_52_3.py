"""The bc-base Dockerfile pins shop-templates at v0.52.3 (lead-e5jx release).

The v0.3.64 release (lead-e5jx) re-pins the bc-base image's shop-templates VCS
pin to v0.52.3 — the canonical ADR-058 dispatcher release (lead-opd8) that
supersedes the v0.52.2 approximation. The rebuilt ghcr bc-base:latest bakes the
shop-templates release that pours the FULL .fabro def INCLUDING the canonical
reactive dispatcher (dispatcher.toml / dispatcher.fabro / dispatch_acp_agent.py).
This test asserts the concrete pinned version (not merely the vMAJOR.MINOR.PATCH
shape that tests/test_bc_base_shop_templates_pin.py already covers).
"""
from tests.support.base_image import _baked_shop_templates_version

EXPECTED_SHOP_TEMPLATES_PIN = "v0.52.3"


def test_bc_base_dockerfile_pins_shop_templates_v0_52_3():
    pin = _baked_shop_templates_version()
    assert pin == EXPECTED_SHOP_TEMPLATES_PIN, (
        "bc-base Dockerfile ARG SHOP_TEMPLATES_VERSION must be "
        f"{EXPECTED_SHOP_TEMPLATES_PIN} so the rebuilt bc-base:latest bakes "
        "the canonical ADR-058 dispatcher shop-templates release (lead-opd8) "
        f"that pours the full .fabro def at launch; got {pin!r}."
    )
