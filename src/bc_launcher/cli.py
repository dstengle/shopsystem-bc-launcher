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
# Load-bearing properties (per lead-9sq; property (c) RESTORED by lead-ew86 /
# ADR-050 D3 / ADR-018 D1-D2):
#   (a) directs the agent to arm Monitor on `shop-msg watch --bc <bc_name>`
#   (b) directs the agent to drain pending inbox via
#       `shop-msg pending inbox --bc <bc_name>`
#   (c) directs the agent to AUTONOMOUSLY PROCESS each pending dispatch through
#       the normal Implementer->Reviewer loop to a Reviewer-gated work_done,
#       without waiting for a human "go".  The ADR-050 --orchestrator split had
#       regressed this to "await user direction", which made a tmux-default BC
#       with pending dispatched inbox work merely LIST those dispatches and
#       then PARK — a headless BC that never gets a human "go" would leave the
#       dispatches stuck pending.  The autonomous drain-AND-process directive
#       is the restored tmux-default engage behavior.
#
# An explicit --startup-prompt 'foo' on the command line is a TOTAL
# override: the template default is not used and no substitution occurs.
DEFAULT_STARTUP_PROMPT_TEMPLATE = (
    "Run your session-start sequence per /workspace/CLAUDE.md: "
    "arm Monitor on shop-msg watch --bc {bc_name}, "
    "then drain pending inbox via shop-msg pending inbox --bc {bc_name}, "
    "then autonomously process each pending dispatch through the normal "
    "Implementer->Reviewer loop to a Reviewer-gated work_done, without "
    "waiting for a human go. Bound this autonomy to the dispatched inbox "
    "work only: emit a work_done solely for a work_id that was dispatched "
    "into the inbox, and synthesize no unrequested follow-on work beyond "
    "what was dispatched."
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

# build_parser split out (re-exported for compat):
from bc_launcher.cli_parser import build_parser  # noqa: F401,E402


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
            # Orchestrator selects the engage tier (lead-cadr).  The canonical
            # surface is --orchestrator {tmux|fabro} (default tmux); the S3
            # --fabro-path flag remains a HIDDEN ALIAS forcing fabro (lead-vwib
            # scenario 76 stays green).
            launch_path=(
                "fabro"
                if (
                    getattr(args, "orchestrator", "tmux") == "fabro"
                    or bool(getattr(args, "fabro_path", False))
                )
                else "tmux"
            ),
            work_id=getattr(args, "work_id", None),
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
