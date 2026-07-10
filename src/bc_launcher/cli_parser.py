"""bc-container argparse parser construction.

Split from bc_launcher/cli.py; re-exported via bc_launcher.cli.
"""
from __future__ import annotations

import argparse

from bc_launcher.cli import DEFAULT_STARTUP_PROMPT_TEMPLATE




def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bc-container",
        description="Manage BC Docker containers for the shopsystem framework.",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    # launch
    p_launch = sub.add_parser("launch", help="Start a BC container")
    p_launch.add_argument("bc_name", help="BC name (e.g. shopsystem-messaging)")
    p_launch.add_argument("--repo-url", help="Git repo URL to clone inside the container")
    p_launch.add_argument(
        "--workspace-mount",
        default=None,
        help=(
            "Path to an existing host working tree to bind-mount at the "
            "container's /workspace. When given, the launch SKIPS the clone "
            "and ALL clone-path provisioning (no bd bootstrap, no "
            "shop-templates re-pour), presenting the host tree unchanged "
            "(its committed .beads registry and poured .claude/skills are "
            "left byte-unchanged). Mutually exclusive in effect with "
            "--repo-url (a workspace-mount launch never clones)."
        ),
    )
    p_launch.add_argument(
        "--mount-docker-socket",
        action="store_true",
        help=(
            "Opt-in lead-only flag: bind-mount the host docker socket "
            "(/var/run/docker.sock) into the container so the launched shop "
            "can drive docker itself. OFF by default — when absent, NO "
            "docker-socket mount is added."
        ),
    )
    p_launch.add_argument(
        "--orchestrator",
        choices=["tmux", "fabro"],
        default="tmux",
        help=(
            "Which orchestrator engages the BC agent AFTER the readiness "
            "barrier passes (lead-cadr, ADR-050). 'tmux' (DEFAULT) engages via "
            "the existing tmux 'agent' send-keys / agent-vault run -- claude "
            "path (scenario 04), unchanged. 'fabro' REPLACES that engage tier "
            "with the fabro run-graph entry: the launcher starts the baked "
            "anthropic-oauth-shim on 127.0.0.1:8788 and writes fabro's "
            "effective settings ([llm.providers.anthropic] "
            "base_url=http://127.0.0.1:8788/v1, adapter=anthropic), then starts "
            "an ephemeral in-container fabro server "
            "(fabro server start --foreground --no-web) and runs the "
            "PERSISTENT reactive dispatcher def against it (fabro run "
            "dispatcher.fabro -I BC_NAME=<bc>, requiring NO --work-id; ADR-058) "
            "as the engage — starting NO tmux 'agent' "
            "send-keys session and NO 'claude' on that path. The native fabro "
            "vault stays __PLACEHOLDER__-only and no real credential is written "
            "(ADR-049); the credential rides agent-vault on the wire. Container "
            "/ credential-proxy / postgres DSN / shop-msg mailbox surfaces are "
            "unchanged on both paths — only the engage tier differs "
            "(ADR-050 D1/D2)."
        ),
    )
    p_launch.add_argument(
        "--fabro-path",
        action="store_true",
        # HIDDEN ALIAS for --orchestrator fabro (lead-vwib S3 flag, superseded
        # by the canonical --orchestrator surface in lead-cadr S4; kept working
        # so vwib's scenario 76 fabro-path launch is not broken).
        help=argparse.SUPPRESS,
    )
    p_launch.add_argument(
        "--work-id",
        default=None,
        help=(
            "IGNORED no-op (ADR-058 D6). The fabro engage is now ONE persistent "
            "reactive dispatcher (fabro run dispatcher.fabro -I BC_NAME=<bc>, "
            "NO -I WORK_ID) that discovers work_ids at RUNTIME, so no launch-"
            "time work id is required on either the fabro or the tmux path. Any "
            "--work-id passed is accepted but ignored."
        ),
    )
    p_launch.add_argument("--shopmsg-dsn", help="SHOPMSG_DSN value for the container")
    p_launch.add_argument(
        "--image",
        help=(
            "Base image to launch the BC container from. Overrides the "
            "BC_IMAGE env var and the built-in default "
            "(ghcr.io/dstengle/shopsystem-bc-base:latest). Precedence: "
            "--image flag > BC_IMAGE env > default."
        ),
    )
    p_launch.add_argument("--network", help="Docker network to attach")
    p_launch.add_argument(
        "--debug",
        action="store_true",
        help=(
            "Surface full Python tracebacks for launch-path errors that "
            "would otherwise be translated to a clean stderr line "
            "(e.g. malformed bc-manifest.yaml fields).  Equivalent to "
            "setting BCLAUNCHER_DEBUG=1 in the environment."
        ),
    )
    p_launch.add_argument(
        "--agent-vault-broker",
        default=None,
        help=(
            "Agent-vault broker proxy-listener address (HTTPS_PROXY target / "
            "readiness probe). Overrides the BCLAUNCHER_AGENT_VAULT_BROKER env "
            "var and the built-in default. An explicit flag wins over any "
            "AGENT_VAULT_ADDR supplied via --env-file."
        ),
    )
    p_launch.add_argument(
        "--env-file",
        default=None,
        help=(
            "Path to a KEY=VALUE env file supplying operator agent-vault "
            "credentials. AGENT_VAULT_ADDR / AGENT_VAULT_TOKEN / "
            "AGENT_VAULT_VAULT / AGENT_VAULT_CA_PEM lines are read and injected "
            "into the container env (the broker CA travels as the public "
            "AGENT_VAULT_CA_PEM env var, materialized by the bc-base "
            "entrypoint); other keys are ignored. Blank lines, '#' comments, an "
            "optional 'export ' prefix, and single/double-quoted values are "
            "tolerated. The token is operator-supplied here and never baked "
            "into source."
        ),
    )
    p_launch.add_argument(
        "--startup-prompt",
        default=None,
        help=(
            "Text to inject into tmux after start. "
            "If omitted, a session-start imperative is injected that "
            "directs the BC agent to arm Monitor on "
            "'shop-msg watch --bc <bc_name>', drain pending inbox via "
            "'shop-msg pending inbox --bc <bc_name>', and then await "
            "user direction. An explicit value is a total override "
            "(no concatenation, no substitution). "
            "Default template: " + DEFAULT_STARTUP_PROMPT_TEMPLATE
        ),
    )

    # attach
    p_attach = sub.add_parser("attach", help="Attach to the BC container tmux session")
    p_attach.add_argument("bc_name", help="BC name")

    # inject
    p_inject = sub.add_parser("inject", help="Send text to the BC container tmux session")
    p_inject.add_argument("bc_name", help="BC name")
    p_inject.add_argument("prompt", help="Text to send")

    # monitor
    p_monitor = sub.add_parser("monitor", help="Stream BC container tmux output")
    p_monitor.add_argument("bc_name", help="BC name")

    # stop
    p_stop = sub.add_parser("stop", help="Stop and remove the BC container")
    p_stop.add_argument("bc_name", help="BC name")

    # status
    p_status = sub.add_parser("status", help="Report BC container state")
    p_status.add_argument("bc_name", help="BC name")

    # start-agent (lead-k4k7) — recovery: drive the agent-start sequence
    # against an already-cloned, healthy container that has no agent, WITHOUT
    # re-cloning.  Idempotent / safe to re-run.
    p_start_agent = sub.add_parser(
        "start-agent",
        help=(
            "Recover an already-cloned, healthy container that has no agent "
            "by driving the agent-start sequence (tmux + agent-vault claude + "
            "inject) WITHOUT re-cloning. Idempotent / safe to re-run."
        ),
    )
    p_start_agent.add_argument("bc_name", help="BC name")
    p_start_agent.add_argument(
        "--shopmsg-dsn",
        default=None,
        help=(
            "SHOPMSG_DSN value for the messaging-readiness barrier. Defaults "
            "to the DSN the container was launched with."
        ),
    )
    p_start_agent.add_argument(
        "--agent-vault-broker",
        default=None,
        help=(
            "Agent-vault broker proxy-listener address for the readiness "
            "probe. Overrides BCLAUNCHER_AGENT_VAULT_BROKER and the default."
        ),
    )
    p_start_agent.add_argument(
        "--startup-prompt",
        default=None,
        help=(
            "Text to inject into tmux after the agent is ready. If omitted, "
            "the default session-start imperative is injected (same as launch)."
        ),
    )

    # list
    sub.add_parser("list", help="List all known BC containers")

    # manifest
    p_manifest = sub.add_parser("manifest", help="Manage the BC manifest file")
    manifest_sub = p_manifest.add_subparsers(dest="manifest_subcommand", required=True)

    # manifest validate
    p_mv = manifest_sub.add_parser("validate", help="Validate the BC manifest")
    p_mv.add_argument(
        "--manifest", default="bc-manifest.yaml",
        help="Path to the manifest file (default: bc-manifest.yaml)",
    )
    p_mv.add_argument(
        "--repos-dir", default=None,
        help="Path to the repos directory for consistency checks",
    )
    p_mv.add_argument(
        "--product-slug", default=None,
        help=(
            "Product slug used to derive the accepted BC-name prefix "
            "'<slug>-<identifier>'. Precedence: this flag -> PRODUCT_SLUG env "
            "-> default 'shopsystem'."
        ),
    )

    # manifest list
    p_ml = manifest_sub.add_parser("list", help="List BCs declared in the manifest")
    p_ml.add_argument(
        "--manifest", default="bc-manifest.yaml",
        help="Path to the manifest file (default: bc-manifest.yaml)",
    )

    # manifest sync
    p_ms = manifest_sub.add_parser("sync", help="Sync repos directory from the manifest")
    p_ms.add_argument(
        "--manifest", default="bc-manifest.yaml",
        help="Path to the manifest file (default: bc-manifest.yaml)",
    )
    p_ms.add_argument(
        "--repos-dir", default="repos",
        help="Path to the repos directory (default: repos)",
    )

    return parser
