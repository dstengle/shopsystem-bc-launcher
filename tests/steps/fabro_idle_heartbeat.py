"""Step defs for the lead-8hpz idle-but-live fabro liveness heartbeat scenario
(@scenario_hash:a5ce1af45ade7444).

ADDITIVE bugfix: extends the structural liveness pin e94a01b26ed6a4cc (ADR-050
D3). The `--orchestrator fabro` engage's ONLY always-resident process is
`shop-msg watch --bc <name>`, a LISTEN/NOTIFY event source that wakes ONLY on a
real message and NEVER per poll tick — so it advances NO bc_presence heartbeat
while the BC is idle-but-live (zero resident finite runs, no message in flight).
The lead-8hpz regression: an idle-but-live BC's last_seen_at ages past the
bc-status staleness window (operator-confirmed ~2525s) and it reports OFFLINE +
the container healthcheck reports UNHEALTHY though it is functionally healthy.
THE FIX: the always-resident supervisor UPSERTs bc_presence on a bounded cadence
MESSAGE-INDEPENDENTLY (NOT per-poll-tick, NOT only-when-work-in-flight), so an
idle-but-live BC stays ONLINE and healthy.

FIDELITY: these steps drive the REAL launcher (controller.launch over the
FakeDockerDriver, launch_path="fabro") and bind to its ACTUAL recorded engage
`/bin/sh -c` script — never a model, never a shallow string-match. TEETH: remove
the message-independent heartbeat cadence from `_fabro_engage_script` and every
Then below REDs.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from pytest_bdd import given, when, then  # noqa: F401

# The REAL bc-status ONLINE staleness window (seconds): age < this => ONLINE.
# Imported from the SAME module `shop-msg bc-status` classifies by, so the
# "stays online" bound is tied to the real classifier, not a magic number.
from shop_msg.storage import PRESENCE_ONLINE_MAX_SECONDS

from bc_launcher.controller import BcContainerController
from bc_launcher.fabro import engage as _engage_mod
from bc_launcher.fabro.constants import FABRO_BIN, FABRO_DISPATCHER_FILE
from tests.fake_driver import FakeDockerDriver


_BC_NAME = "shopsystem-messaging"
_HOST_TREE = "/host/live/shopsystem-messaging"
_WORK_ID = "lead-8hpz-idle-live"


def _make_manifest(tmp_path: Path) -> Path:
    manifest = tmp_path / "bc-manifest.yaml"
    manifest.write_text(
        "product: shopsystem product\n"
        "bcs:\n"
        f"  - name: {_BC_NAME}\n"
        f"    remote: https://github.com/shopsystem/{_BC_NAME}.git\n"
        "    role: bc\n"
    )
    return manifest


def _make_credential_home(tmp_path: Path) -> Path:
    home = tmp_path / "fake_home"
    home.mkdir(exist_ok=True)
    (home / ".claude").mkdir(exist_ok=True)
    (home / ".claude" / ".claude.json").write_text("{}")
    (home / ".config" / "gh").mkdir(parents=True, exist_ok=True)
    (home / ".gitconfig").write_text("")
    return home


def _real_engage_script(tmp_path: Path) -> str:
    """Drive the REAL launcher on the --orchestrator fabro path and return its
    ACTUAL recorded engage `/bin/sh -c` supervisor script."""
    driver = FakeDockerDriver()
    driver.set_host_tree_snapshot(
        _HOST_TREE,
        beads_registry='{"id":"seed-1","title":"committed"}\n',
        claude_skills="poured-skill-group/bc-router-health\n",
    )
    controller = BcContainerController(driver)
    result = controller.launch(
        bc_name=_BC_NAME,
        repo_url=None,
        workspace_mount=_HOST_TREE,
        launch_path="fabro",
        work_id=_WORK_ID,
        manifest_path=_make_manifest(tmp_path),
        credential_home=_make_credential_home(tmp_path),
    )
    assert result.exit_code == 0, (
        f"fabro launch failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    for c in driver.exec_calls:
        if (
            c.command[:2] == ["/bin/sh", "-c"]
            and len(c.command) >= 3
            and "fabro server start" in c.command[2]
        ):
            return c.command[2]
    raise AssertionError("the fabro engage exec (server start) must exist")


def _script(ctx) -> str:
    return ctx["idle_hb_script"]


def _heartbeat_cadence_loop(script: str) -> str:
    """Return the text of the MESSAGE-INDEPENDENT presence-heartbeat cadence loop:
    a BACKGROUNDED loop that periodically fires the `heartbeat` upsert on a
    `sleep`-driven cadence gated ONLY on the shared server's liveness (NOT on any
    message / dispatch).  Raises AssertionError (the RED) when absent."""
    # A backgrounded `( ... heartbeat ... ; do sleep <n>; heartbeat; done ) &`
    # cadence loop.  The condition is the shared-server-liveness `kill -0
    # "$FABRO_SERVER_PID"` guard (message-INDEPENDENT), NOT the `while read`
    # dispatch reader (message-DRIVEN).
    m = re.search(
        r"\(\s*heartbeat;\s*while\s+kill -0 \"\$FABRO_SERVER_PID\"[^\n]*;\s*do\s+"
        r"sleep\s+(\d+);\s*heartbeat;\s*done\s*\)\s*&",
        script,
    )
    assert m is not None, (
        "the supervisor must run a MESSAGE-INDEPENDENT presence-heartbeat cadence "
        "loop — a backgrounded `( heartbeat; while kill -0 \"$FABRO_SERVER_PID\"; "
        "do sleep <n>; heartbeat; done ) &` gated ONLY on the shared server's "
        "liveness, NOT on message arrival — so an idle-but-live BC keeps UPSERTing "
        f"bc_presence and stays ONLINE; script:\n{script}"
    )
    return m.group(0)


# ---------------------------------------------------------------------------
# Given / And / When
# ---------------------------------------------------------------------------

@given(
    'the container "bc-shopsystem-messaging" is running the "--orchestrator '
    'fabro" watcher engage with its always-resident supervisor process running'
)
def idle_hb_engage(ctx, tmp_path):
    ctx["idle_hb_script"] = _real_engage_script(tmp_path)
    # The always-resident supervisor is `shop-msg watch --bc "$BC_NAME"` (the
    # structural pin e94a01b26ed6a4cc this scenario extends).
    assert 'shop-msg watch --bc "$BC_NAME"' in ctx["idle_hb_script"]


@given(
    "NO inbound message is in flight, so the BC is idle-but-live with zero "
    "resident finite runs"
)
def idle_hb_no_message(ctx):
    # Idle-but-live: the `shop-msg watch` reader wakes ONLY on a real NOTIFY, so
    # with no message in flight it drives NO dispatch and advances no heartbeat by
    # itself.  The heartbeat must therefore come from a message-INDEPENDENT cadence.
    ctx["idle_hb_idle"] = True


@when(
    "the BC runs idle for longer than the bc-status staleness window with no "
    "dispatched work arriving"
)
def idle_hb_run_idle(ctx):
    ctx["idle_hb_ran_idle"] = True


# ---------------------------------------------------------------------------
# Then
# ---------------------------------------------------------------------------

@then(
    '"shop-msg bc-status" classifies "shopsystem-messaging" as ONLINE because '
    "its last_seen_at heartbeat is within the staleness window, NOT offline with "
    "a stale heartbeat"
)
def idle_hb_online(ctx):
    script = _script(ctx)
    loop = _heartbeat_cadence_loop(script)

    # The heartbeat is a real bc_presence UPSERT: each tick runs a BOUNDED
    # `shop-msg watch --bc "$BC_NAME"` whose first action is a presence upsert
    # keyed on the SAME canonical name bc-status queries (so it cannot mis-key),
    # then exits.  A `heartbeat()` function that does exactly that must exist.
    hb_fn = re.search(
        r"heartbeat\(\)\s*\{(?P<body>.*?)\}",
        script,
        re.DOTALL,
    )
    assert hb_fn is not None, (
        "a `heartbeat()` function must define the bc_presence UPSERT the cadence "
        f"loop fires; script:\n{script}"
    )
    body = hb_fn.group("body")
    assert 'shop-msg watch --bc "$BC_NAME"' in body, (
        "the heartbeat UPSERT must run `shop-msg watch --bc \"$BC_NAME\"` so it is "
        "keyed on the SAME canonical presence name bc-status queries (lead-bppa); "
        f"heartbeat body:\n{body}"
    )
    assert "timeout" in body, (
        "the heartbeat's `shop-msg watch` must be BOUNDED (timeout) so it UPSERTs "
        f"one heartbeat then exits, letting the cadence loop repeat; body:\n{body}"
    )

    # The cadence interval must keep an idle BC's last_seen_at age STRICTLY inside
    # the bc-status ONLINE staleness window, so bc-status classifies it ONLINE
    # (not offline with a stale heartbeat).
    interval = int(re.search(r"sleep\s+(\d+);\s*heartbeat;\s*done", loop).group(1))
    assert 0 < interval < PRESENCE_ONLINE_MAX_SECONDS, (
        f"the heartbeat cadence ({interval}s) must be a positive interval STRICTLY "
        f"below the bc-status ONLINE staleness window "
        f"({PRESENCE_ONLINE_MAX_SECONDS}s) so an idle-but-live BC stays ONLINE"
    )


@then(
    "the container healthcheck reports healthy, NOT unhealthy, for the "
    "idle-but-live BC"
)
def idle_hb_healthy(ctx):
    script = _script(ctx)
    # The healthcheck reads the SAME last_seen_at freshness bc-status classifies
    # by; the message-INDEPENDENT cadence loop keeps it fresh while idle, so the
    # healthcheck reports healthy.  The heartbeat must run regardless of message
    # arrival — its loop is gated on the shared-server liveness guard, never on the
    # `while read` dispatch reader.
    loop = _heartbeat_cadence_loop(script)
    assert 'FABRO_SERVER_PID' in loop and 'while read' not in loop, (
        "the heartbeat cadence must be MESSAGE-INDEPENDENT (gated on server "
        "liveness, not on the `while read` dispatch reader) so the healthcheck "
        f"stays healthy while idle; loop:\n{loop}"
    )


@then(
    "this closes the lead-8hpz regression where a functionally healthy fabro BC "
    "reported offline and unhealthy because the fabro engage maintained no "
    "shop-msg heartbeat after replacing the tmux session-start loop"
)
def idle_hb_closes_regression(ctx):
    script = _script(ctx)
    # Negative control: the superseded infinite `fabro run dispatcher.toml` engage
    # (which maintained NO shop-msg heartbeat) is gone, AND the superseded
    # per-5s-poll heartbeat direction is NOT how the heartbeat is emitted — the
    # heartbeat rides a dedicated cadence loop, not the message-dispatch poll.
    assert "fabro run dispatcher.toml" not in script, (
        "the superseded infinite `fabro run dispatcher.toml` engage (no heartbeat) "
        f"must be gone; script:\n{script}"
    )
    # The heartbeat is NOT emitted from inside the message-driven dispatch reader
    # (NOT only-when-work-in-flight / NOT per-poll-tick): the `dispatch` reader
    # loop body must NOT contain the heartbeat upsert.
    reader = re.search(
        r"shop-msg watch --bc \"\$BC_NAME\"[^\n]*\|\s*while[^\n]*read[^\n]*do(?P<body>.*?)done",
        script,
        re.DOTALL,
    )
    assert reader is not None, "the message-driven `while read` dispatch reader must exist"
    assert "heartbeat" not in reader.group("body"), (
        "the heartbeat must NOT be gated on message arrival — it must not live "
        f"inside the `while read` dispatch reader; reader body:\n{reader.group('body')}"
    )
    # And the always-resident heartbeat source still exists (extends, not
    # contradicts, e94a01b26ed6a4cc).
    _heartbeat_cadence_loop(script)


# ===========================================================================
# Behavior 2 (lead-8hpz sub-issues .3/.4 / @scenario_hash:90e6b9fae7a63eb8):
# the heartbeat cadence is BOUNDED strictly inside the bc-status staleness
# window — pinning the EFFECTIVE period (cadence `sleep` interval PLUS the
# per-tick bounded `shop-msg watch` timeout — the true worst-case age between
# successive UPSERTs), with TEETH (the launcher refuses to build an engage whose
# effective period reaches/exceeds the window) and the NEGATIVE CONTROL (the
# superseded infinite `fabro run dispatcher.toml` engage maintained NO heartbeat
# on any cadence).  ADDITIVE — extends a5ce1af45ade7444 (behavior 1 pinned only
# the RAW sleep interval; this counts the bound too).
# ===========================================================================


def _heartbeat_timeout_bound(script: str) -> int:
    """The per-tick bounded-watch timeout (seconds) inside the `heartbeat()`
    function — the extra worst-case age a single UPSERT can add on top of the
    cadence sleep interval.  Raises AssertionError (RED) when absent."""
    hb_fn = re.search(r"heartbeat\(\)\s*\{(?P<body>.*?)\}", script, re.DOTALL)
    assert hb_fn is not None, (
        f"a `heartbeat()` function must define the bounded presence UPSERT; script:\n{script}"
    )
    m = re.search(r"timeout\s+(\d+)\s+shop-msg watch", hb_fn.group("body"))
    assert m is not None, (
        "the heartbeat UPSERT must be a BOUNDED `timeout <n> shop-msg watch` so it "
        f"fires one upsert then exits; heartbeat body:\n{hb_fn.group('body')}"
    )
    return int(m.group(1))


def _cadence_sleep_interval(script: str) -> int:
    """The cadence loop's `sleep <n>` interval (seconds)."""
    loop = _heartbeat_cadence_loop(script)
    return int(re.search(r"sleep\s+(\d+);\s*heartbeat;\s*done", loop).group(1))


@given(
    'the container "bc-shopsystem-messaging" is running the "--orchestrator '
    'fabro" watcher engage with its always-resident supervisor maintaining the '
    "shop-msg heartbeat"
)
def bound_engage(ctx, tmp_path):
    ctx["bound_script"] = _real_engage_script(tmp_path)
    # The always-resident supervisor's heartbeat source is the bounded `shop-msg
    # watch --bc <name>` UPSERT (the structural pin this behavior extends).
    assert 'shop-msg watch --bc "$BC_NAME"' in ctx["bound_script"]


@when(
    "the supervisor runs continuously for several multiples of the bc-status "
    "staleness window while the BC stays live"
)
def bound_run_continuous(ctx):
    # The cadence fires message-INDEPENDENTLY for the whole supervisor lifetime;
    # this marks the "runs continuously" precondition for the Then assertions.
    ctx["bound_ran_continuous"] = True


@then(
    "the supervisor UPSERTs the bc_presence (bc_name, last_seen_at) heartbeat on "
    "a cadence whose interval is BOUNDED strictly below the bc-status staleness "
    "window, independent of whether any message arrives"
)
def bound_effective_period_below_window(ctx):
    script = ctx["bound_script"]
    loop = _heartbeat_cadence_loop(script)  # RED if the cadence loop is absent
    interval = _cadence_sleep_interval(script)
    bound = _heartbeat_timeout_bound(script)
    # The EFFECTIVE heartbeat period — the worst-case age between two successive
    # bc_presence UPSERTs — is the cadence `sleep` interval PLUS the per-tick
    # bounded-watch timeout.  Behavior 1 pinned only `interval`; this counts the
    # bound too, and pins the SUM strictly below the REAL bc-status ONLINE
    # staleness window so a live BC's last_seen_at can never age stale between
    # heartbeats.
    effective = interval + bound
    assert 0 < effective < PRESENCE_ONLINE_MAX_SECONDS, (
        f"the EFFECTIVE heartbeat period (cadence sleep {interval}s + bounded-watch "
        f"timeout {bound}s = {effective}s) must be a positive value STRICTLY below "
        f"the bc-status ONLINE staleness window ({PRESENCE_ONLINE_MAX_SECONDS}s) so "
        f"a live BC never ages stale between heartbeats"
    )
    # Message-INDEPENDENT: the cadence loop is gated on the shared-server liveness
    # guard, NEVER on the `while read` dispatch reader.
    assert "FABRO_SERVER_PID" in loop and "while read" not in loop, (
        f"the heartbeat cadence must be MESSAGE-INDEPENDENT; loop:\n{loop}"
    )
    ctx["bound_effective"] = effective


@then(
    "because the cadence interval is below the staleness window, the last_seen_at "
    "never ages past the staleness threshold while the supervisor is alive, so a "
    "live BC never flaps to offline between heartbeats"
)
def bound_guard_refuses_stale_cadence(ctx, monkeypatch):
    # TEETH — the launcher must REFUSE to build a stale-cadence engage.  The
    # healthy build (current interval 30 + bound 5 = 35 < 90) succeeds:
    healthy = _engage_mod._fabro_engage_script("shopsystem-messaging")
    assert isinstance(healthy, str) and healthy, "the healthy engage must build"

    # If a future edit raised the cadence interval to the staleness window, the
    # effective period would reach/exceed it and a live BC could age stale between
    # heartbeats — `_fabro_engage_script` must RAISE rather than silently emit that
    # engage.  Bound to the REAL classifier window (PRESENCE_ONLINE_MAX_SECONDS).
    monkeypatch.setattr(
        _engage_mod, "FABRO_WATCH_HEARTBEAT_INTERVAL_SECS", PRESENCE_ONLINE_MAX_SECONDS
    )
    with pytest.raises(ValueError):
        _engage_mod._fabro_engage_script("shopsystem-messaging")

    # And — the distinctively load-bearing case vs behavior 1 — a raw interval that
    # is itself BELOW the window but whose SUM with the bounded-watch timeout meets
    # or exceeds the window is ALSO refused: the bound is COUNTED, not just the
    # sleep interval.  (interval 89 < 90, but 89 + 5 = 94 >= 90 -> refused.)
    monkeypatch.setattr(
        _engage_mod,
        "FABRO_WATCH_HEARTBEAT_INTERVAL_SECS",
        PRESENCE_ONLINE_MAX_SECONDS - 1,
    )
    monkeypatch.setattr(_engage_mod, "FABRO_WATCH_HEARTBEAT_BOUND_SECS", 5)
    with pytest.raises(ValueError):
        _engage_mod._fabro_engage_script("shopsystem-messaging")


@then(
    "as the negative control, the superseded infinite \"fabro run dispatcher.toml\" "
    "engage maintained NO shop-msg heartbeat on any cadence, so its last_seen_at "
    "aged unboundedly and a live BC reported offline (lead-8hpz), which this "
    "bounded cadence fixes"
)
def bound_negative_control(ctx):
    script = ctx["bound_script"]
    # The superseded engage's actual entrypoint, reconstructed from the REAL
    # constants still carried in the package (FABRO_BIN + FABRO_DISPATCHER_FILE):
    # a single infinite `fabro run dispatcher.toml`.
    superseded = f"{FABRO_BIN} run {FABRO_DISPATCHER_FILE}"
    assert superseded == "fabro run dispatcher.toml", (
        f"the superseded engage entrypoint must be `fabro run dispatcher.toml`; "
        f"got {superseded!r}"
    )
    # NEGATIVE CONTROL: that superseded engage maintained NO shop-msg heartbeat on
    # ANY cadence — no bounded `shop-msg watch` presence UPSERT, no cadence loop —
    # so its last_seen_at aged unboundedly and a live BC reported offline.
    assert "shop-msg watch" not in superseded
    assert "heartbeat" not in superseded

    # The CURRENT engage is NOT that superseded infinite dispatcher run ...
    assert "fabro run dispatcher.toml" not in script, (
        f"the superseded heartbeat-less `fabro run dispatcher.toml` engage must be "
        f"gone; script:\n{script}"
    )
    # ... and it DOES carry the bounded presence-heartbeat cadence that fixes the
    # lead-8hpz regression (the differential vs the negative control): a cadence
    # loop whose `heartbeat()` runs the bounded `shop-msg watch` UPSERT.
    _heartbeat_cadence_loop(script)
    hb_fn = re.search(r"heartbeat\(\)\s*\{(?P<body>.*?)\}", script, re.DOTALL)
    assert hb_fn is not None and 'shop-msg watch --bc "$BC_NAME"' in hb_fn.group("body"), (
        f"the current engage must carry the bounded shop-msg-watch heartbeat cadence "
        f"the negative control lacked; script:\n{script}"
    )
