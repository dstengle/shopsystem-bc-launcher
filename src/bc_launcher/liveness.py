"""The single CROSS-RUNTIME liveness contract both engage runtimes obey
(lead-8hpz behavior 3 / @scenario_hash:81eee7115a2457f4).

The launcher engages a BC under one of two runtimes — the tmux/claude
session-start loop (DEFAULT) or the ``--orchestrator fabro`` watcher supervisor.
Behaviors 1&2 (a5ce1af45ade7444 / 90e6b9fae7a63eb8) gave the fabro runtime the
SAME message-independent ``shop-msg watch``->bc_presence heartbeat the tmux
session-start loop already maintained.  This module is the ONE place that names
that shared liveness contract, so the two runtimes cannot silently DIVERGE on the
liveness surface:

  * ``PRESENCE_HEARTBEAT_WATCH_VERB`` — the canonical presence-heartbeat command
    verb BOTH runtimes maintain their bc_presence heartbeat with.  The tmux
    session-start loop arms ``<verb> <name>`` inside the live claude agent; the
    fabro supervisor fires ``<verb> "$BC_NAME"`` on its message-independent
    cadence.  Both keep the bc_presence ``last_seen_at`` fresh while idle-but-live.

  * ``PRESENCE_ONLINE_MAX_SECONDS`` / ``classify_presence_age`` — RE-EXPORTED
    (the SAME objects, not divergent copies) from ``shop_msg.storage`` so BOTH
    runtimes' liveness is read through the EXACT classifier ``shop-msg bc-status``
    classifies by.  An idle-but-live BC (fresh heartbeat) is ONLINE on either
    runtime; a genuinely DEAD BC (no further heartbeat upsert) ages past the
    window and is OFFLINE on either runtime — identically — so the liveness
    signal stays a TRUE liveness signal, never a runtime-faked "always online",
    and an operator cannot distinguish runtime from the liveness surface.
"""
from __future__ import annotations

# The SAME classifier objects `shop-msg bc-status` classifies presence by — the
# single shared oracle both engage runtimes are read through (never a divergent
# duplicate literal / copy).
from shop_msg.storage import (  # noqa: F401  (re-exported as the shared contract)
    PRESENCE_ONLINE_MAX_SECONDS,
    classify_presence_age,
)

# The canonical presence-heartbeat command verb BOTH runtimes maintain their
# bc_presence heartbeat with.  Each runtime appends its own name form (the tmux
# session-start loop the literal ``<name>``; the fabro supervisor the shell
# ``"$BC_NAME"``), but the VERB is one shared source so the fabro liveness
# interface MIRRORS the tmux one rather than diverging from it.
PRESENCE_HEARTBEAT_WATCH_VERB = "shop-msg watch --bc"


def presence_heartbeat_command(bc_name: str) -> str:
    """The presence-heartbeat command for ``bc_name`` — ``<verb> <bc_name>``.

    The canonical spelling a runtime uses when it maintains the bc_presence
    heartbeat with a literal BC name (the tmux session-start loop).  Runtimes
    that key the heartbeat off a shell variable append their own ``"$BC_NAME"``
    to :data:`PRESENCE_HEARTBEAT_WATCH_VERB` directly.
    """
    return f"{PRESENCE_HEARTBEAT_WATCH_VERB} {bc_name}"
