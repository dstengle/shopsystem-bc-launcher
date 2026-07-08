#!/usr/bin/env python3
"""dispatch_acp_agent.py -- NON-LLM ACP script-agent for dispatcher.fabro's
`dispatch` node (ADR-058 Amendment 2, lead-3zzu).

fabro drives this node via backend="acp" + acp.command="python3
dispatch_acp_agent.py": fabro speaks the agent-client-protocol (crate v0.11.1)
JSON-RPC over stdio -- initialize -> session/new -> session/prompt. Fabro injects
NO model credentials into an ACP agent, and this agent runs NO model: it is a
pure python SCRIPT, so the dispatch step burns ZERO tokens.

CONTEXT-IN / DECISIONS-OUT. Each session/prompt delivers the poll context -- the
pending inbox work ids plus the in-flight run state (which work_ids have a live
child not yet work_done). `decide` returns structured dispatch DECISIONS the
loop consumes: {work_id, action} with action SPAWN or SKIP.

(acpkind stage: `decide` returns a decision per pending id. The in-flight SKIP
semantics land in the idempotency behavior; the per-child WORK_ID env overlay +
detached spawn land in the delivery behavior.)
"""
from __future__ import annotations

import json
import sys

ACP_PROTOCOL_VERSION = 1


def decide(pending_ids, in_flight):
    """Return structured dispatch DECISIONS for the poll context.

    ``pending_ids`` -- the pending inbox work ids yielded by the poll node.
    ``in_flight``   -- the set of work_ids whose prior child is still running and
                       has not yet emitted work_done.

    Each decision is a {"work_id", "action"} record; ``action`` is "SPAWN" or
    "SKIP".  (acpkind: every pending id is a decision; the in-flight SKIP rule is
    the idempotency behavior.)
    """
    decisions = []
    for wid in pending_ids:
        decisions.append({"work_id": wid, "action": "SPAWN"})
    return decisions


# --------------------------------------------------------------------------
# ACP JSON-RPC stdio handshake (initialize -> session/new -> session/prompt).
# Non-LLM: no model, no credentials. The handlers are pure and unit-testable.
# --------------------------------------------------------------------------

def handle_initialize(_params):
    return {
        "protocolVersion": ACP_PROTOCOL_VERSION,
        "agentCapabilities": {},
        "serverInfo": {"name": "dispatch_acp_agent", "version": "2"},
    }


def handle_session_new(_params):
    return {"sessionId": "dispatch"}


def _parse_context(params):
    """Pull the poll context (pending ids + in-flight state) out of a
    session/prompt request.  The context rides the prompt as a JSON object with
    keys ``pending`` and ``in_flight``."""
    pending, in_flight = [], set()
    for block in (params or {}).get("prompt", []) or []:
        text = block.get("text") if isinstance(block, dict) else None
        if not text:
            continue
        try:
            payload = json.loads(text)
        except (ValueError, TypeError):
            continue
        pending = list(payload.get("pending", []) or [])
        in_flight = set(payload.get("in_flight", []) or [])
    return pending, in_flight


def handle_session_prompt(params):
    pending, in_flight = _parse_context(params)
    decisions = decide(pending, in_flight)
    return {"stopReason": "end_turn", "decisions": decisions}


_HANDLERS = {
    "initialize": handle_initialize,
    "session/new": handle_session_new,
    "session/prompt": handle_session_prompt,
}


def _dispatch_rpc(request):
    method = request.get("method")
    handler = _HANDLERS.get(method)
    resp = {"jsonrpc": "2.0", "id": request.get("id")}
    if handler is None:
        resp["error"] = {"code": -32601, "message": f"method not found: {method}"}
    else:
        resp["result"] = handler(request.get("params") or {})
    return resp


def main(stdin=None, stdout=None):
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            continue
        if "id" not in request:
            continue  # a notification -- no response
        resp = _dispatch_rpc(request)
        stdout.write(json.dumps(resp) + "\n")
        stdout.flush()


if __name__ == "__main__":
    main()
