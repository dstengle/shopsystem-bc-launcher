"""
Shared primitive constants for the bc_launcher package.

This is a dependency-free leaf module (it imports nothing from the rest of the
package).  It holds the handful of primitive constants that are shared across
more than one bc_launcher module — most importantly the container-layout and
broker-CA-trust primitives that BOTH ``controller`` and ``fabro`` reference.
Keeping them here gives a single source of truth and lets ``fabro`` import them
without creating a ``controller <-> fabro`` import cycle (both modules depend on
this leaf, never on each other).

``controller`` re-exports every name below, so the historical
``from bc_launcher.controller import CONTAINER_WORKSPACE`` (and the other three)
import paths keep resolving unchanged.
"""
from __future__ import annotations

# Container workspace root — the in-container path every bind-mount, clone,
# and def-placement is anchored under.
CONTAINER_WORKSPACE = "/workspace"

# The container user that owns the agent tmux session and all of its clients
# (send-keys, capture-pane, has-session, attach-session).  The BC image's
# default USER is root; Claude Code refuses --dangerously-skip-permissions when
# EUID==0 for security reasons, so the agent must run as a non-root user.
# vscode is the unprivileged user already provisioned in the BC base image with
# HOME=/home/vscode (the same home into which credential mounts and cp steps
# land).
AGENT_CONTAINER_USER = "vscode"

# The container CA path the bc-base entrypoint materializes the broker CA to is
# FIXED by the operator design (bclaunch-9rr).  Both the clone path (via
# GIT_SSL_CAINFO) and the fabro shim/engage path (via SSL_CERT_FILE) point their
# TLS trust at this same materialized path so brokered HTTPS verifies without a
# login shell.
AGENT_VAULT_CONTAINER_CA_PATH = "/home/vscode/.config/agent-vault/ca.pem"

# Env var name python/urllib consults for its CA bundle.  The fabro shim +
# engage execs run via NON-LOGIN /bin/sh -c, so they never source the login
# profile that would export this; the controller/fabro wiring sets it explicitly
# to AGENT_VAULT_CONTAINER_CA_PATH (lead-ze4w BUG#3).
SSL_CERT_FILE_ENV = "SSL_CERT_FILE"

# --- core container / image / messaging identity primitives ---
# (moved from controller; shared across controller and sibling modules)

# Host path of the docker socket, bind-mounted into the container ONLY when the
# opt-in lead-only docker-socket flag is enabled (lead-zxtk,
# @scenario_hash:ff370a4e7e9dac5e / e177655ba09a73fa).
DOCKER_SOCKET_PATH = "/var/run/docker.sock"
AGENT_TMUX_SESSION = "agent"
BC_IMAGE = "ghcr.io/dstengle/shopsystem-bc-base:latest"
BC_IMAGE_ENV = "BC_IMAGE"
SHOPMSG_DSN_ENV = "SHOPMSG_DSN"
