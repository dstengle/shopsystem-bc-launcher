#!/bin/sh
# bc-base INTERACTIVE BOOTSTRAP entrypoint mode (lead-f6xs).
#
# This is a MODE of the EXISTING bc-base lineage image — NOT a separate,
# purpose-built bootstrap image. The same published bc-base image that runs the
# brokered steady-state agent (ENTRYPOINT agent-vault-ca.sh -> CMD that wraps
# the agent as `agent-vault run -- claude`) is ALSO run, with this bootstrap
# entrypoint selected, to perform the one-time HUMAN authentication beat.
#
# WHY a distinct beat. The brokered steady-state run is fully unattended: the
# image carries only a SYNTHETIC, placeholder-only logged-in Claude state (the
# baked nested-claudeAiOauth .credentials.json whose accessToken is the literal
# "__PLACEHOLDER__") and the broker substitutes the REAL Claude OAuth / GitHub
# credentials on the wire via `agent-vault run -- claude`. That placeholder wrap
# is correct for steady state but useless for the FIRST login: a human must
# interactively authenticate `claude` and `gh` once so the broker has real
# credentials to hold. This bootstrap beat is that one-time human-in-the-loop
# step.
#
# CONTRACT (lead-f6xs scenario 20b7a66364a26404):
#   * `claude` is invoked INTERACTIVELY, attached to the host TTY, so the human
#     can complete the Claude login flow. It is NOT wrapped as
#     `agent-vault run -- claude` — there is no broker substitution context in
#     the bootstrap beat; the human authenticates directly.
#   * `gh auth login` is invoked INTERACTIVELY, attached to the host TTY, so the
#     human can complete the GitHub device/web login flow.
#   * This beat places NO "__PLACEHOLDER__" credential as the Claude or GitHub
#     credential. The placeholder is the steady-state brokered artifact; the
#     bootstrap beat's whole point is to obtain REAL human credentials, so it
#     must never write/seed/leave a __PLACEHOLDER__ token as the operative
#     Claude or GitHub credential.
#
# CONTRACT (lead-f6xs scenario 938342272de4e38a):
#   * Because this is a mode of the existing bc-base image, the four baked
#     framework CLIs — shop-templates, shop-msg, bc-container, agent-vault —
#     resolve on PATH inside this running container EXACTLY as they do for a
#     brokered steady-state run. They are baked once into the bc-base image
#     (the pip VCS pins + the agent-vault binary install in the Dockerfile);
#     this entrypoint adds nothing to and removes nothing from that PATH
#     resolution. We assert their presence at start as a fail-fast guard so a
#     bootstrap container that somehow lost the baked CLIs surfaces it loudly
#     rather than authenticating into a broken steady state.

set -e

echo "bc-base interactive bootstrap entrypoint: human authentication beat (lead-f6xs)"

# --- framework CLIs resolve on PATH exactly as for a brokered run -----------
# The bootstrap mode is a mode of the existing bc-base lineage image; the four
# baked framework CLIs must resolve on PATH the same way they do for a brokered
# steady-state run. Fail fast if any is missing.
for cli in shop-templates shop-msg bc-container agent-vault; do
    if ! command -v "$cli" >/dev/null 2>&1; then
        echo "bootstrap entrypoint: framework CLI '$cli' not found on PATH; \
this is the existing bc-base lineage image and must carry the baked CLIs" >&2
        exit 1
    fi
done

# --- Claude interactive authentication (NOT the brokered placeholder wrap) ---
# Invoke `claude` INTERACTIVELY attached to the host TTY for the human to
# authenticate. This is deliberately NOT `agent-vault run -- claude`: the
# bootstrap beat has no broker substitution context, the human logs in directly.
echo "bootstrap: launching interactive 'claude' for human authentication (attached to TTY)"
claude </dev/tty >/dev/tty 2>&1

# --- GitHub interactive authentication --------------------------------------
# Invoke `gh auth login` INTERACTIVELY attached to the host TTY so the human can
# complete the GitHub login flow.
echo "bootstrap: launching interactive 'gh auth login' for human authentication (attached to TTY)"
gh auth login </dev/tty >/dev/tty 2>&1

# NOTE: this bootstrap beat intentionally places NO "__PLACEHOLDER__" credential
# as the Claude or GitHub credential. The __PLACEHOLDER__ token is the
# steady-state brokered artifact (baked into the image for unattended runs); the
# bootstrap beat obtains REAL human credentials and must never seed a placeholder
# as the operative Claude/GitHub credential here.

echo "bc-base interactive bootstrap entrypoint: human authentication beat complete"
