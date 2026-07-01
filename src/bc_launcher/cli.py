"""
bc-container CLI entry point.

Subcommands: launch, attach, inject, monitor, stop, status, list, manifest
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from bc_launcher.controller import BcContainerController, _resolve_shop_network
from bc_launcher.driver import RealDockerDriver
from bc_launcher.manifest import ManifestController, RealGitDriver, RealGitHubDriver


# Default --startup-prompt for `bc-container launch`.
#
# When the operator omits --startup-prompt, this template (with {bc_name}
# substituted) is injected into the BC's tmux session as the first user
# prompt.  Claude Code does not execute SessionStart hook output as an
# imperative; a synthetic first user prompt is the only mechanism that
# directs the BC agent to act before a human types.
#
# Load-bearing properties (per lead-9sq):
#   (a) directs the agent to arm Monitor on `shop-msg watch --bc <bc_name>`
#   (b) directs the agent to drain pending inbox via
#       `shop-msg pending inbox --bc <bc_name>`
#   (c) ends with "await user direction" so the agent does not synthesize
#       follow-on work.
#
# An explicit --startup-prompt 'foo' on the command line is a TOTAL
# override: the template default is not used and no substitution occurs.
DEFAULT_STARTUP_PROMPT_TEMPLATE = (
    "Run your session-start sequence per /workspace/CLAUDE.md: "
    "arm Monitor on shop-msg watch --bc {bc_name}, "
    "then drain pending inbox via shop-msg pending inbox --bc {bc_name}, "
    "then await user direction."
)


def _is_closed_quote(value: str) -> bool:
    """True if ``value`` is a single-physical-line quoted string whose opening
    quote has a matching closing quote on the same line.

    Used to distinguish a complete single-line quoted value (e.g. ``"abc"``)
    from a quoted value left open for multi-line continuation (e.g. ``"-----``).
    """
    return (
        len(value) >= 2
        and value[0] in ("'", '"')
        and value[-1] == value[0]
    )


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse a KEY=VALUE env file into a dict.

    Tolerates blank lines, '#' comments, an optional leading 'export ', and
    single/double-quoted values.  Surrounding whitespace around the key and the
    (unquoted) value is stripped.  Lines without '=' are skipped.

    Multi-line quoted values (lead-b14a): a quoted value whose opening quote is
    NOT closed on the same physical line continues accumulating subsequent
    physical lines -- with their real newlines preserved -- until the matching
    closing quote.  This lets a multi-line broker CA PEM travel through
    AGENT_VAULT_CA_PEM intact (the old ``splitlines()`` parser truncated it at
    the first physical newline).  The materialized value is the verbatim
    multi-line string; the bc-base entrypoint's ``printf '%s\\n'`` reproduces
    it byte-for-byte, so both ends agree on real newlines (no ``\\n``-escape
    convention is introduced).  Single-line quoted/unquoted values are
    unchanged.
    """
    result: dict[str, str] = {}
    # Keep raw physical lines (no whitespace stripping of the value body yet)
    # so a multi-line quoted value preserves its internal newlines and per-line
    # content exactly.
    lines = path.read_text().splitlines()
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i].strip()
        i += 1
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()

        # Detect an opening quote that is not closed on this physical line.
        # When found, accumulate following physical lines (joined by the real
        # newline that ``splitlines()`` removed) until the closing quote
        # appears, then drop the surrounding quotes.
        if value[:1] in ("'", '"') and not _is_closed_quote(value):
            quote = value[0]
            collected = value[1:]  # drop the opening quote
            while i < n:
                nxt = lines[i]
                i += 1
                if quote in nxt:
                    closing = nxt.index(quote)
                    collected += "\n" + nxt[:closing]
                    break
                collected += "\n" + nxt
            value = collected
        elif _is_closed_quote(value):
            value = value[1:-1]

        if key:
            result[key] = value
    return result


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
        "--fabro-path",
        action="store_true",
        help=(
            "Launch on the FABRO ORCHESTRATOR path instead of the default "
            "ADR-050 tmux/engage-tier path. On the fabro path the launcher "
            "additionally starts the baked anthropic-oauth-shim as an "
            "in-container background listener on 127.0.0.1:8788 and writes "
            "fabro's effective settings ([llm.providers.anthropic] "
            "base_url=http://127.0.0.1:8788/v1, adapter=anthropic) so fabro's "
            "built-in anthropic provider routes through the shim. The native "
            "fabro vault stays __PLACEHOLDER__-only and no real credential is "
            "written (ADR-049); the credential rides agent-vault on the wire. "
            "OFF by default — the tmux launch default is unchanged."
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


def _run_manifest(args: argparse.Namespace) -> int:
    """Handle 'bc-container manifest <subcommand>'."""
    mc = ManifestController(
        github_driver=RealGitHubDriver(),
        git_driver=RealGitDriver(),
    )
    manifest_path = Path(args.manifest)

    if args.manifest_subcommand == "validate":
        repos_dir = Path(args.repos_dir) if args.repos_dir else None
        result = mc.validate(
            manifest_path,
            repos_dir=repos_dir,
            product_slug=getattr(args, "product_slug", None),
        )
        for msg in result.messages:
            sys.stdout.write(msg + "\n")
        return 0 if result.ok else 1

    elif args.manifest_subcommand == "list":
        exit_code, output = mc.list_bcs(manifest_path)
        sys.stdout.write(output)
        return exit_code

    elif args.manifest_subcommand == "sync":
        repos_dir = Path(args.repos_dir)
        exit_code, output = mc.sync(manifest_path, repos_dir)
        sys.stdout.write(output)
        return exit_code

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.subcommand == "manifest":
        return _run_manifest(args)

    controller = BcContainerController(RealDockerDriver())

    if args.subcommand == "launch":
        # Resolve --startup-prompt: explicit value is a total override;
        # omission produces the default session-start imperative with the
        # BC name substituted into the template.
        explicit_prompt = getattr(args, "startup_prompt", None)
        if explicit_prompt is None:
            startup_prompt = DEFAULT_STARTUP_PROMPT_TEMPLATE.format(
                bc_name=args.bc_name
            )
        else:
            startup_prompt = explicit_prompt
        debug = bool(getattr(args, "debug", False)) or bool(
            os.environ.get("BCLAUNCHER_DEBUG")
        )

        # Resolve operator agent-vault credentials (bclaunch-3le).
        #
        # --env-file supplies AGENT_VAULT_ADDR / AGENT_VAULT_TOKEN /
        # AGENT_VAULT_VAULT; the broker has a dedicated --agent-vault-broker
        # flag.  Precedence (documented): for the broker, an explicit
        # --agent-vault-broker flag wins over AGENT_VAULT_ADDR from the
        # env-file.  For ADDR/TOKEN/VAULT the env-file is the supply channel
        # (no dedicated per-value flags).  controller.launch() further falls
        # back to the like-named process-env vars when a value is None.
        env_file_path = getattr(args, "env_file", None)
        env_vals: dict[str, str] = {}
        if env_file_path:
            env_vals = _parse_env_file(Path(env_file_path))

        # Export any AGENT_VAULT_* keys the env-file supplied (notably the
        # public AGENT_VAULT_CA_PEM broker CA) into the process env so
        # controller.launch()'s AGENT_VAULT_* pass-through forwards them into
        # the container env.  The broker CA now travels as this env var (no
        # --agent-vault-ca path flag, no controller bind-mount).  The token
        # remains operator-supplied and is never baked into source.
        for key, value in env_vals.items():
            if key.startswith("AGENT_VAULT_"):
                os.environ.setdefault(key, value)

        result = controller.launch(
            bc_name=args.bc_name,
            repo_url=getattr(args, "repo_url", None),
            shopmsg_dsn=getattr(args, "shopmsg_dsn", None),
            image=getattr(args, "image", None),
            startup_prompt=startup_prompt,
            network=getattr(args, "network", None),
            # On-disk shop network fallback (lead-ngzl): resolved from the
            # shop's known on-disk configuration so a manifest lacking a
            # shop-level network/product field does not hard-error.  Only used
            # when no explicit --network and no manifest product is present.
            shop_network=_resolve_shop_network(),
            agent_vault_broker=getattr(args, "agent_vault_broker", None),
            agent_vault_addr=env_vals.get("AGENT_VAULT_ADDR"),
            agent_vault_token=env_vals.get("AGENT_VAULT_TOKEN"),
            agent_vault_vault=env_vals.get("AGENT_VAULT_VAULT"),
            workspace_mount=getattr(args, "workspace_mount", None),
            mount_docker_socket=bool(getattr(args, "mount_docker_socket", False)),
            launch_path=(
                "fabro" if bool(getattr(args, "fabro_path", False)) else "tmux"
            ),
            debug=debug,
        )
        sys.stdout.write(result.stdout)
        if result.stderr:
            sys.stderr.write(result.stderr)
        return result.exit_code

    elif args.subcommand == "attach":
        # attach replaces the process — no return value
        controller.attach(args.bc_name)
        return 0  # unreachable in production

    elif args.subcommand == "inject":
        result = controller.inject(args.bc_name, args.prompt)
        sys.stdout.write(result.stdout)
        if result.stderr:
            sys.stderr.write(result.stderr)
        return result.exit_code

    elif args.subcommand == "monitor":
        result = controller.monitor(args.bc_name)
        sys.stdout.write(result.stdout)
        if result.stderr:
            sys.stderr.write(result.stderr)
        return result.exit_code

    elif args.subcommand == "stop":
        result = controller.stop(args.bc_name)
        sys.stdout.write(result.stdout)
        if result.stderr:
            sys.stderr.write(result.stderr)
        return result.exit_code

    elif args.subcommand == "status":
        result = controller.status(args.bc_name)
        sys.stdout.write(result.stdout)
        if result.stderr:
            sys.stderr.write(result.stderr)
        return result.exit_code

    elif args.subcommand == "start-agent":
        # Resolve --startup-prompt the same way launch does: an explicit value
        # is a total override; omission injects the default session-start
        # imperative with the BC name substituted in.
        explicit_prompt = getattr(args, "startup_prompt", None)
        if explicit_prompt is None:
            startup_prompt = DEFAULT_STARTUP_PROMPT_TEMPLATE.format(
                bc_name=args.bc_name
            )
        else:
            startup_prompt = explicit_prompt
        result = controller.start_agent(
            bc_name=args.bc_name,
            startup_prompt=startup_prompt,
            shopmsg_dsn=getattr(args, "shopmsg_dsn", None),
            agent_vault_broker=getattr(args, "agent_vault_broker", None),
        )
        sys.stdout.write(result.stdout)
        if result.stderr:
            sys.stderr.write(result.stderr)
        return result.exit_code

    elif args.subcommand == "list":
        result = controller.list_containers()
        sys.stdout.write(result.stdout)
        if result.stderr:
            sys.stderr.write(result.stderr)
        return result.exit_code

    return 0


if __name__ == "__main__":
    sys.exit(main())
