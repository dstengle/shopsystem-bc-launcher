"""pytest-bdd step defs for the bc-base PID 1 reaping-init feature (lead-xnop).

BC-INTERNAL structural hardening: parses the committed bc-base Dockerfile
CONTENT (docker build is NOT run — docker is unavailable in this environment),
asserting `tini` is installed and that the ENTRYPOINT is the tini reaping init
in exec form WRAPPING the agent-vault CA entrypoint script (so PID 1 reaps
orphaned children while CA materialization is preserved). See the feature file
for the full rationale.

Reuses the shared Given ("the shopsystem-bc-launcher BC repository") and When
("the bc-base Dockerfile in that repository is inspected") steps from
tests.steps.base_image; only the reaping-init Then steps live here.
"""
from __future__ import annotations

import json
import re

from pytest_bdd import parsers, then

_AGENT_VAULT_CA_SCRIPT = "/usr/local/bin/agent-vault-ca.sh"


def _entrypoint_exec_list(text: str) -> list[str] | None:
    """Return the JSON-exec-form ENTRYPOINT token list, or None if not exec form."""
    m = re.search(r"^\s*ENTRYPOINT\s+(\[.*\])\s*$", text, re.MULTILINE)
    if not m:
        return None
    try:
        parsed = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, list) and all(isinstance(tok, str) for tok in parsed):
        return parsed
    return None


@then("the Dockerfile installs the tini reaping-init binary")
def then_dockerfile_installs_tini(ctx):
    dockerfile = ctx.get("bc_base_dockerfile")
    assert dockerfile is not None, "No bc-base Dockerfile found"
    text = dockerfile.read_text()
    # Collapse Dockerfile backslash line-continuations so a multi-line
    # `RUN apt-get update \ && apt-get install ... tini` reads as one logical
    # line. tini must be a real apt install layer, not merely a comment mention:
    # search only for `tini` as an apt-get install target.
    logical = re.sub(r"\\\s*\n\s*", " ", text)
    assert re.search(r"apt-get install[^\n]*\btini\b", logical), (
        "bc-base Dockerfile has no apt install layer for `tini`; PID 1 needs a "
        "reaping init baked in to reap orphaned <defunct> children"
    )


@then("the Dockerfile ENTRYPOINT is the tini reaping init in exec form")
def then_entrypoint_is_tini(ctx):
    dockerfile = ctx.get("bc_base_dockerfile")
    assert dockerfile is not None, "No bc-base Dockerfile found"
    text = dockerfile.read_text()
    tokens = _entrypoint_exec_list(text)
    assert tokens is not None, (
        "bc-base Dockerfile ENTRYPOINT is not in JSON exec form; a shell-form "
        "ENTRYPOINT would insert /bin/sh as PID 1 (no reaping)"
    )
    assert tokens[0] == "tini", (
        f"bc-base ENTRYPOINT PID 1 is {tokens[0]!r}, not `tini`; PID 1 must be "
        "the reaping init so orphaned children are reaped regardless of run flags"
    )
    assert "--" in tokens, (
        "tini ENTRYPOINT is missing the `--` argument separator before its child"
    )


@then("the tini ENTRYPOINT wraps the agent-vault CA entrypoint script as its child")
def then_tini_wraps_agent_vault(ctx):
    dockerfile = ctx.get("bc_base_dockerfile")
    assert dockerfile is not None, "No bc-base Dockerfile found"
    text = dockerfile.read_text()
    tokens = _entrypoint_exec_list(text)
    assert tokens is not None, "bc-base ENTRYPOINT is not in JSON exec form"
    assert _AGENT_VAULT_CA_SCRIPT in tokens, (
        f"tini ENTRYPOINT does not wrap {_AGENT_VAULT_CA_SCRIPT!r} as its child; "
        "the CA-materialization entrypoint must be preserved (wrapped, not "
        "replaced) so CA trust + baked CLIs still run on container start"
    )
    # The CA script must be the child tini execs — i.e., appear AFTER `--`.
    assert "--" in tokens and tokens.index(_AGENT_VAULT_CA_SCRIPT) > tokens.index("--"), (
        "the agent-vault CA script must be the child tini execs (after `--`), "
        "not a tini flag"
    )


@then(parsers.parse('the CMD remains "{cmd}" so the wrapped entrypoint keeps '
                    'the container alive'))
def then_cmd_remains(ctx, cmd):
    dockerfile = ctx.get("bc_base_dockerfile")
    assert dockerfile is not None, "No bc-base Dockerfile found"
    text = dockerfile.read_text()
    # The final top-level CMD directive is the container's default command. A
    # HEALTHCHECK instruction also embeds a `CMD [...]` clause; take the LAST
    # line-leading CMD match so we target the real default command, not the
    # healthcheck probe.
    matches = re.findall(r"^\s*CMD\s+(\[.*\])\s*$", text, re.MULTILINE)
    assert matches, "bc-base Dockerfile has no JSON exec-form CMD"
    parsed = json.loads(matches[-1])
    assert parsed == cmd.split(), (
        f"bc-base CMD is {parsed!r}, expected {cmd.split()!r}; tini + "
        "agent-vault-ca.sh must still exec CMD to keep the container alive"
    )
