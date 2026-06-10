#!/bin/sh
# bc-base in-container HEALTHCHECK probe (bclaunch-wuo).
#
# WHY THIS EXISTS
# ---------------
# A BC container is only useful when the two services it strictly depends on
# are reachable from inside it:
#
#   1. the agent-vault BROKER — the SOLE credential path (ADR-026). The agent
#      runs `agent-vault run -- claude`; every outbound HTTPS request is
#      routed through the broker's proxy listener (carried in-container as the
#      HTTPS_PROXY env var, e.g. http://agent-vault:14321). A container whose
#      broker is unreachable can authenticate to nothing — it is unhealthy
#      even though `sleep infinity` (PID 1) is alive.
#
#   2. the messaging DATABASE — carried in-container as SHOPMSG_DSN. A BC whose
#      messaging DB is unreachable cannot drain its inbox or emit responses.
#
# This script is wired as the image HEALTHCHECK (see the Dockerfile HEALTHCHECK
# instruction). Docker runs it on the container's health interval; its exit
# code drives `docker inspect`'s .State.Health.Status:
#     exit 0  -> healthy
#     exit 1  -> unhealthy
# The host reads that status via RealDockerDriver.health_status (controller
# health()).  Before this probe existed the image carried NO HEALTHCHECK, so
# .State.Health was absent and docker-inspect reported "none" — the
# broker-down / DB-down "unhealthy" behavior was fake-driver-only.
#
# PROBE TARGETS (runtime env, NOT bake-time — the broker host:port and DSN are
# operator-supplied at `docker run -e` time, which this process inherits):
#   broker:  host:port parsed from HTTPS_PROXY (fallback AGENT_VAULT_ADDR)
#   db:      host:port parsed from SHOPMSG_DSN
#
# Each target is checked with a dependency-free TCP connect. python3 is always
# present (the base image is a python:3.11 devcontainer); we do NOT rely on nc.

set -eu

# tcp_reachable <address> -- parse host/port out of a URL-or-host:port address
# and attempt a TCP connect. Returns 0 (reachable) / 1 (unreachable/unparseable).
tcp_reachable() {
    addr="$1"
    [ -n "$addr" ] || return 1
    BCHC_ADDR="$addr" python3 - <<'PY'
import os, socket, sys
from urllib.parse import urlparse

addr = os.environ.get("BCHC_ADDR", "")
if "://" not in addr:
    addr = "tcp://" + addr
parsed = urlparse(addr)
host = parsed.hostname
port = parsed.port
if not host or not port:
    sys.exit(1)
try:
    with socket.create_connection((host, port), timeout=2.0):
        sys.exit(0)
except OSError:
    sys.exit(1)
PY
}

# Broker: the address the container actually routes outbound HTTPS through.
BROKER_ADDR="${HTTPS_PROXY:-${AGENT_VAULT_ADDR:-}}"
if ! tcp_reachable "$BROKER_ADDR"; then
    echo "unhealthy: agent-vault broker unreachable at ${BROKER_ADDR:-<unset>}" >&2
    exit 1
fi

# Messaging database: required for inbox/outbox transport.
if ! tcp_reachable "${SHOPMSG_DSN:-}"; then
    echo "unhealthy: messaging database unreachable at ${SHOPMSG_DSN:-<unset>}" >&2
    exit 1
fi

echo "healthy: broker and messaging database reachable"
exit 0
