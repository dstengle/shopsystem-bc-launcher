"""
bc-container CLI entry point.

Subcommands: launch, attach, inject, monitor, stop, status, list, manifest
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bc_launcher.controller import BcContainerController
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
    p_launch.add_argument("--shopmsg-dsn", help="SHOPMSG_DSN value for the container")
    p_launch.add_argument("--network", help="Docker network to attach")
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
        result = mc.validate(manifest_path, repos_dir=repos_dir)
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
        result = controller.launch(
            bc_name=args.bc_name,
            repo_url=getattr(args, "repo_url", None),
            shopmsg_dsn=getattr(args, "shopmsg_dsn", None),
            startup_prompt=startup_prompt,
            network=getattr(args, "network", None),
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

    elif args.subcommand == "list":
        result = controller.list_containers()
        sys.stdout.write(result.stdout)
        if result.stderr:
            sys.stderr.write(result.stderr)
        return result.exit_code

    return 0


if __name__ == "__main__":
    sys.exit(main())
