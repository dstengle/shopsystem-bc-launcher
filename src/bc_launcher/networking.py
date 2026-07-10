"""Shop docker-network resolution + agent-vault probe-broker address.

Extracted verbatim from ``controller`` (Phase 1 of the controller.py
decomposition). Leaf module; re-exported by ``controller`` for import-path
compatibility. Do not import ``controller`` from here (would cycle).
"""
from __future__ import annotations

from pathlib import Path

import yaml

from bc_launcher.naming import _slugify
from bc_launcher.agent_vault import (
    AGENT_VAULT_CONTROL_API_PORT,
    AGENT_VAULT_SERVICE_NAME,
    DEFAULT_AGENT_VAULT_BROKER,
)


# SHOPMSG_SYSTEM_SLUG (lead-53y0): bc-launcher RESOLVES + INJECTS this slug
# into the launched BC container's docker run env.  bc-launcher itself NEVER
# reads/consumes SHOPMSG_SYSTEM_SLUG — the CONSUMER is the BC's own shop-msg
# at runtime (messaging, lead-tgsb).  Resolution precedence for the injected
# value: SHOPMSG_SYSTEM_SLUG env on the launcher invocation > manifest
# product: > DEFAULT_SYSTEM_SLUG ('shopsystem').
SHOPMSG_SYSTEM_SLUG_ENV = "SHOPMSG_SYSTEM_SLUG"

DEFAULT_SYSTEM_SLUG = "shopsystem"



def _resolve_shop_network(start_dir: Path | None = None) -> str | None:
    """Resolve the shop's docker network name from on-disk shop configuration.

    This is the fallback network source (lead-ngzl): when no explicit
    ``--network`` is given AND bc-manifest.yaml carries no shop-level
    network/product field, the network is resolved from the shop's known
    on-disk configuration rather than hard-erroring.  Per ADR-038 D3 the
    precedence is: explicit override > manifest product > on-disk shop
    network / hard default ``"shopsystem"``.

    SEQUENCING (lead-ngzl / ADR-043 D2): the canonical single-source
    ops-coordinates artifact (``bin/ops-coordinates``) does not exist yet
    (lead-7wta).  In the interim this resolves the network name from, in
    order:

      1. ``compose.yaml`` ``networks:`` — the first network entry that
         declares an explicit ``name:`` (the live shop wiring:
         ``networks: shopsystem: {name: shopsystem}``);
      2. the product slug from ``.claude/shop/name.md`` with a trailing
         ``-product`` suffix stripped (``"shopsystem-product"`` ->
         ``"shopsystem"``).

    Returns the resolved network name, or ``None`` when no on-disk shop
    network configuration is discoverable (the error path is then the
    caller's responsibility when ``--network`` is also absent).
    """
    import yaml

    base = start_dir or Path.cwd()

    # (1) compose.yaml networks: <key>: {name: <network>}
    compose_path = base / "compose.yaml"
    if compose_path.exists():
        try:
            data = yaml.safe_load(compose_path.read_text())
        except yaml.YAMLError:
            data = None
        if isinstance(data, dict):
            networks = data.get("networks")
            if isinstance(networks, dict):
                for spec in networks.values():
                    if isinstance(spec, dict):
                        name = spec.get("name")
                        if isinstance(name, str) and name.strip():
                            return name.strip()

    # (2) product slug from .claude/shop/name.md minus a "-product" suffix
    name_md = base / ".claude" / "shop" / "name.md"
    if name_md.exists():
        raw = name_md.read_text().strip()
        if raw:
            token = raw.splitlines()[0].strip()
            if token.endswith("-product"):
                token = token[: -len("-product")]
            if token:
                return _slugify(token)

    return None



def resolve_probe_broker_address(
    explicit_broker: str | None,
    system_slug: str | None,
) -> str:
    """Resolve the agent-vault broker address used for the READINESS PROBE.

    lead-cs7k DEFECT (b): the readiness probe must target the broker by a host
    the LAUNCHED CONTAINER's network can resolve.  The pre-fix code probed the
    hardcoded ``DEFAULT_AGENT_VAULT_BROKER`` (``http://agent-vault:14321``),
    which only resolves on the single-product ``shopsystem`` network.  For a
    SECOND product the broker lives on the product network under a
    slug-qualified name (e.g. ``dummyco-agent-vault``), so the probe host must
    DERIVE from the resolved product slug.

    Crucially this PROBE address is DECOUPLED from the runtime ``HTTPS_PROXY``
    value (``_build_runtime_proxy_url``): pointing the probe at
    ``dummyco-agent-vault:14321`` (the control-API reachability target) must
    NOT clobber the ``http://<token>:<vault>@<host>:14322`` MITM proxy the
    launched agent uses verbatim.  This function therefore returns ONLY the
    probe address and is never fed into the runtime-proxy env.

    Precedence:
      1. An explicit operator-supplied broker URL wins verbatim (it already
         names the broker the operator wants probed).
      2. Else, when a product slug is known, the probe host is
         ``<slug>-agent-vault`` on the control-API port.
      3. Else (no slug) the unqualified default broker.
    """
    if explicit_broker:
        return explicit_broker
    if system_slug and system_slug != DEFAULT_SYSTEM_SLUG:
        host = f"{_slugify(system_slug)}-{AGENT_VAULT_SERVICE_NAME}"
        return f"http://{host}:{AGENT_VAULT_CONTROL_API_PORT}"
    return DEFAULT_AGENT_VAULT_BROKER
