"""
Unit test for bclaunch-97w: DEFAULT_AGENT_VAULT_BROKER must point at the
provisioned broker's real listener port (14321), not the stale :8080.

GAP (bclaunch-97w): controller.py defaulted DEFAULT_AGENT_VAULT_BROKER to
'http://agent-vault:8080' but the provisioned broker listens on :14321.  The
readiness barrier is a bare TCP connect, so with the stale default it fails
against the real broker.  This pins the corrected default.
"""
from __future__ import annotations

from bc_launcher.controller import DEFAULT_AGENT_VAULT_BROKER


def test_default_agent_vault_broker_points_at_14321():
    assert DEFAULT_AGENT_VAULT_BROKER == "http://agent-vault:14321"


def test_default_agent_vault_broker_is_not_the_stale_8080():
    assert ":8080" not in DEFAULT_AGENT_VAULT_BROKER
