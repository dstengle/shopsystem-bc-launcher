#!/bin/sh
# agent-vault broker CA materialization + TLS-trust export (bclaunch-9rr).
#
# The broker substitutes credentials by intercepting outbound HTTPS
# (HTTPS_PROXY -> TLS MITM). Without the broker CA trusted inside the
# container, node (claude), python (requests/openssl), git and curl HTTPS calls
# through the proxy FAIL cert verification.
#
# Under the operator no-bind-mount design directive (bclaunch-7pf REVISED) the
# launcher does NOT mount the CA. Instead the operator-supplied PUBLIC broker
# CA PEM (~574 bytes, NOT secret) arrives as the container env var
# AGENT_VAULT_CA_PEM (passed via `docker run -e`, so visible to BOTH PID 1 and
# every `docker exec` session). This script materializes it to a file and
# exports the five trust vars pointing at that file.
#
# This script is used TWICE:
#   1. As the image ENTRYPOINT (runs once on container start as PID 1).
#   2. Installed into /etc/profile.d/ so that login/exec shells re-materialize
#      the CA if missing and re-export the trust vars. This is REQUIRED because
#      the agent runs via `docker exec ... tmux ... agent-vault run -- claude`,
#      which does NOT inherit the entrypoint PID 1's process-local exports — so
#      the agent's shell and the `agent-vault run` subshell would otherwise not
#      see the trust vars.
#
# CRITICAL fixed values (operator design — do not change):
#   container CA path: /home/vscode/.config/agent-vault/ca.pem
#   env var name:      AGENT_VAULT_CA_PEM
#   trust vars:        GIT_SSL_CAINFO SSL_CERT_FILE NODE_EXTRA_CA_CERTS
#                      REQUESTS_CA_BUNDLE CURL_CA_BUNDLE

AGENT_VAULT_CA_PATH="/home/vscode/.config/agent-vault/ca.pem"

# Materialize the CA file when missing, then export the trust vars.
#
# CA-CONTENT SOURCE PRECEDENCE (lead-z0v2 regression fix):
#   1. the inline AGENT_VAULT_CA_PEM env (ADR-045) when set + non-empty;
#   2. ELSE the working operator path `agent-vault ca fetch` — the SAME
#      workaround an operator runs by hand (`agent-vault ca fetch > <ca>`).
#
# REGRESSION (lead-z0v2): previously the CA file was written ONLY when
# AGENT_VAULT_CA_PEM was set, so a real flagless launch (empty PEM env) left
# the file absent while the launcher still pointed git at it — the clone failed
# "error setting certificate file: .../ca.pem".  The `agent-vault ca fetch`
# fallback guarantees real CA *content* lands on disk even with no inline PEM.
if [ ! -s "${AGENT_VAULT_CA_PATH}" ]; then
    mkdir -p /home/vscode/.config/agent-vault
    if [ -n "${AGENT_VAULT_CA_PEM:-}" ]; then
        # (1) inline PEM (ADR-045) — guarded on AGENT_VAULT_CA_PEM being set.
        printf '%s\n' "${AGENT_VAULT_CA_PEM}" > "${AGENT_VAULT_CA_PATH}"
    elif command -v agent-vault >/dev/null 2>&1; then
        # (2) operator path: fetch the broker CA from the running broker.
        agent-vault ca fetch > "${AGENT_VAULT_CA_PATH}" 2>/dev/null || true
    fi
    # vscode-owned: the agent runs as vscode and must be able to read it.
    chown -R vscode:vscode /home/vscode/.config/agent-vault 2>/dev/null || true
fi

# Export the five trust vars pointing at the CA ONLY when a non-empty CA file
# actually exists — never point tooling at a CA path we failed to write.
if [ -s "${AGENT_VAULT_CA_PATH}" ]; then
    # Export the five trust vars pointing at the materialized CA so node
    # (claude), python (requests/openssl), git and curl all trust the broker.
    export GIT_SSL_CAINFO="${AGENT_VAULT_CA_PATH}"
    export SSL_CERT_FILE="${AGENT_VAULT_CA_PATH}"
    export NODE_EXTRA_CA_CERTS="${AGENT_VAULT_CA_PATH}"
    export REQUESTS_CA_BUNDLE="${AGENT_VAULT_CA_PATH}"
    export CURL_CA_BUNDLE="${AGENT_VAULT_CA_PATH}"
fi

# When invoked as the ENTRYPOINT (args present), exec the container command so
# CMD / `docker run` overrides still work. When sourced from /etc/profile.d
# (no args), just fall through after exporting.
if [ "$#" -gt 0 ]; then
    exec "$@"
fi
