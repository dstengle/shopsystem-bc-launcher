"""lead-6ev8 behavior 1 (@scenario_hash:3b3cf899ddd8ed68) — the fabro LLM/ACP
node must RETRY-AND-SURVIVE a transient 429 burst: its workflow-level retry
semantics must be max_attempts > 1, so a single transient error is NOT terminal.

EMPIRICAL ROOT CAUSE (reconciled at the REAL fabro v0.254.0 mechanism, not a
model).  The committed ``workflow.fabro`` ALREADY carried ``retry=4`` (classify)
/ ``retry=3`` (judgment nodes) per lead-i0wi F1, YET the lead-6ev8 dogfood run
(01KXF5XB24R1RXDX4KEESVVC53) showed the ``bc-router classify`` LLM node running
with max_attempts=1 and failing-fast to a content-free ``emit_blk`` on the FIRST
transient 429 (~14s), despite the oauth-shim showing ``200`` then ``429 x4``
(infra sound).

The discrepancy is explained by running the REAL fabro binary: ``retry=N`` is NOT
a recognized fabro node attribute — it is silently ignored, leaving the node at
max_attempts=1 (fail-fast).  The recognized node-level retry-budget attribute is
``max_retries=N``, which the runtime honors as max_attempts = N+1.  Proven by a
real ``fabro run`` (dry-run): a probe node with ``retry=4`` emits ``stage.started``
max_attempts=1, while the same node with ``max_retries=4`` emits max_attempts=5.

So the fix is to give the LLM/ACP nodes the EFFECTIVE ``max_retries=N`` budget
(max_attempts > 1) — the ineffective ``retry=N`` stays (lead-i0wi F1 pins it) but
is inert on its own.

FIDELITY (run the REAL tool, do not reimplement):
  * ``test_fabro_runtime_*`` runs the REAL fabro binary over minimal probe graphs
    and observes the emitted ``stage.started`` max_attempts, with the NEGATIVE
    CONTROL that the pre-fix ``retry=N`` attribute yields max_attempts=1 while
    ``max_retries=N`` yields max_attempts=N+1 (> 1).  It SKIPs honestly only if
    the fabro binary genuinely cannot be obtained or a local server cannot be
    started — it never papers a failure over.
  * ``test_committed_classify_*`` binds that proven-effective attribute to the
    committed def: the committed ``classify`` node's declared retry budget, run
    through the REAL fabro binary, yields max_attempts > 1 (retry-and-survive).
  * ``test_llm_acp_nodes_carry_effective_max_retries_budget`` (static teeth) pins
    that EVERY LLM/ACP agent node carries ``max_retries=N`` (N>=1) so no LLM node
    is left at the inert-``retry=`` / max_attempts=1 fail-fast posture.

This is NOT a model and NOT a shallow string-match: the runtime legs execute the
real fabro binary and read the max_attempts it emits.  ADDITIVE: references (does
not re-pin) the lead-01jw.3 diagnostic scenarios and the lead-i0wi retry work.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from bc_launcher.controller import _fabro_def_asset_root
from tests.support.container import _ky63_locate_or_fetch_fabro


_FEATURE = (
    Path(__file__).resolve().parent.parent
    / "features"
    / "bc_container_fabro_llm_transient_retry.feature"
)
_BEHAVIOR_1_HASH = "3b3cf899ddd8ed68"

# The LLM/ACP agent nodes whose 429 fail-fast is the lead-6ev8 bug.  These are
# the model-backed judgment/classify nodes (they carry a `class=` + `prompt=`),
# as opposed to the deterministic native `script=` nodes.
_LLM_ACP_NODES = ("classify", "suff", "plan", "impl", "review", "impl_f")


# ---- REAL committed def helpers (shared shape with lead-i0wi / lead-01jw.3) --

def _workflow_text() -> str:
    """The REAL committed workflow.fabro bytes (the placed def)."""
    return (_fabro_def_asset_root() / "workflow.fabro").read_text()


def _node_body(graph: str, name: str) -> str:
    """Return the ``name [ ... ]`` attribute body for a node, scanning the
    matching ``]`` quote-aware so a shell ``[ ... ]`` inside a script= string
    does not close the node early."""
    m = re.search(rf"(?m)^\s*{re.escape(name)}\s*\[", graph)
    assert m is not None, f"node {name!r} not found in workflow.fabro"
    i = m.end() - 1
    depth = 0
    inq = False
    j = i
    while j < len(graph):
        c = graph[j]
        if inq:
            if c == "\\":
                j += 2
                continue
            if c == '"':
                inq = False
        else:
            if c == '"':
                inq = True
            elif c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    return graph[i + 1:j]
        j += 1
    raise AssertionError(f"unterminated node body for {name!r}")


def _max_retries(body: str) -> int | None:
    """The node's declared ``max_retries=N`` budget (the attribute the REAL
    fabro runtime honors as max_attempts=N+1), or None if absent.  A bare
    ``retry=N`` (which the runtime silently ignores) is NOT matched."""
    m = re.search(r"\bmax_retries=(\d+)", body)
    return int(m.group(1)) if m else None


def _scenario_blocks(text: str) -> dict[str, str]:
    """Block-only scenario extraction (ADR-019): the Scenario line + steps, with
    the @scenario_hash tag line and the enclosing Feature line EXCLUDED."""
    lines = text.splitlines()
    blocks: dict[str, str] = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        m = (
            re.search(r"@scenario_hash:([0-9a-f]+)", line)
            if line.lstrip().startswith("@")
            else None
        )
        if m:
            tag_hash = m.group(1)
            j = i + 1
            while j < len(lines) and lines[j].lstrip().startswith("@"):
                j += 1
            assert lines[j].lstrip().startswith("Scenario"), (
                f"Expected a Scenario line after the hash tag; got {lines[j]!r}"
            )
            start = j
            j += 1
            while j < len(lines):
                stripped = lines[j].lstrip()
                if stripped.startswith("@") or stripped.startswith("Scenario"):
                    break
                j += 1
            end = j
            while end > start + 1 and lines[end - 1].strip() == "":
                end -= 1
            blocks[tag_hash] = "\n".join(lines[start:end]) + "\n"
            i = j
            continue
        i += 1
    return blocks


# ===========================================================================
# REAL fabro runtime harness — start an isolated ephemeral fabro server and run
# a probe graph, returning the probe node's emitted max_attempts.  SKIPs
# honestly if the binary cannot be obtained or the server cannot be started.
# ===========================================================================

def _dev_token() -> str:
    return "fabro_dev_" + os.urandom(32).hex()


def _probe_graph(node_attr: str) -> str:
    """A minimal one-real-node graph whose ``probe`` node carries ``node_attr``
    (e.g. ``max_retries=4`` or ``retry=4``)."""
    return (
        "digraph P {\n"
        '    graph [ goal="probe retry budget", fallback_retry_target="halt" ]\n'
        '    start [shape=Mdiamond, label="Start"]\n'
        '    done  [shape=Msquare, label="done"]\n'
        f'    probe [shape=parallelogram, {node_attr}, label="probe", '
        'script="true"]\n'
        '    halt  [shape=parallelogram, label="halt", script="exit 1"]\n'
        "    start -> probe\n"
        "    probe -> done\n"
        '    probe -> halt [condition="outcome=failed"]\n'
        "}\n"
    )


class _FabroRuntime:
    """An isolated, authenticated, ephemeral fabro server the probe runs
    against.  Everything (config, storage, socket, credential store) lives under
    one temp FABRO_HOME so nothing collides with the host fabro."""

    def __init__(self, fabro: str, home: Path):
        self.fabro = fabro
        self.home = home
        self.sock = home / "fabro.sock"
        self.proc: subprocess.Popen | None = None
        self.token = _dev_token()

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["FABRO_HOME"] = str(self.home)
        env["FABRO_NO_UPGRADE_CHECK"] = "1"
        env["FABRO_SERVER"] = str(self.sock)
        return env

    def start(self) -> bool:
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "storage").mkdir(exist_ok=True)
        (self.home / "settings.toml").write_text(
            '[server.auth]\nmethods = ["dev-token"]\n'
        )
        env = self._env()
        env["FABRO_DEV_TOKEN"] = self.token
        env["SESSION_SECRET"] = os.urandom(32).hex()
        log = (self.home / "server.log").open("wb")
        self.proc = subprocess.Popen(
            [
                self.fabro, "server", "start", "--foreground", "--no-web",
                "--config", str(self.home / "settings.toml"),
                "--bind", str(self.sock),
                "--storage-dir", str(self.home / "storage"),
            ],
            env=env, stdout=log, stderr=subprocess.STDOUT,
        )
        # Poll for the socket to appear (server ready).
        for _ in range(60):
            if self.proc.poll() is not None:
                return False  # server died during startup
            if self.sock.exists():
                break
            time.sleep(0.5)
        else:
            return False
        # Authenticate the client against the isolated server.
        login = subprocess.run(
            [
                self.fabro, "auth", "login", "--server", str(self.sock),
                "--dev-token", self.token, "--no-upgrade-check",
            ],
            env=env, capture_output=True, text=True, timeout=60,
        )
        return login.returncode == 0

    def probe_max_attempts(self, node_attr: str) -> int:
        """Run a probe graph whose ``probe`` node carries ``node_attr`` and
        return the max_attempts the REAL fabro runtime emits for that node."""
        graph_path = self.home / f"probe_{abs(hash(node_attr))}.fabro"
        graph_path.write_text(_probe_graph(node_attr))
        proc = subprocess.run(
            [
                self.fabro, "run", str(graph_path), "--dry-run", "--json",
                "--no-upgrade-check", "--server", str(self.sock),
            ],
            env=self._env(), capture_output=True, text=True, timeout=120,
        )
        assert proc.returncode == 0, (
            f"real `fabro run` failed for probe {node_attr!r}: "
            f"rc={proc.returncode}\nstdout={proc.stdout[-2000:]!r}\n"
            f"stderr={proc.stderr[-1000:]!r}"
        )
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("event") == "stage.started" and ev.get("node_id") == "probe":
                ma = (ev.get("properties") or {}).get("max_attempts")
                assert ma is not None, (
                    f"stage.started for probe carried no max_attempts: {ev!r}"
                )
                return int(ma)
        raise AssertionError(
            f"no probe stage.started event in `fabro run` output for "
            f"{node_attr!r}; stdout={proc.stdout[-2000:]!r}"
        )

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=15)


@pytest.fixture(scope="module")
def fabro_runtime(tmp_path_factory):
    """A module-scoped isolated fabro server the runtime legs probe against."""
    fabro, note = _ky63_locate_or_fetch_fabro()
    if fabro is None:
        pytest.skip(
            f"fabro binary could not be obtained; real-runtime retry-semantics "
            f"legs deferred honestly. reason: {note!r}"
        )
    home = tmp_path_factory.mktemp("fabro6ev8home")
    rt = _FabroRuntime(fabro, home)
    if not rt.start():
        log = ""
        try:
            log = (home / "server.log").read_text()[-1500:]
        except OSError:
            pass
        rt.stop()
        pytest.skip(
            "a local ephemeral fabro server could not be started for the "
            f"real-runtime retry-semantics legs (honest SKIP). server.log tail:"
            f"\n{log}"
        )
    try:
        yield rt
    finally:
        rt.stop()


# ===========================================================================
# Scenario-hash pin (block-only recompute must equal the on-disk tag).
# ===========================================================================

@pytest.mark.skipif(
    shutil.which("scenarios") is None,
    reason="canonical `scenarios` CLI not on PATH",
)
def test_scenario_block_recomputes_to_its_pin():
    """The block-only hash of scenario 3b3cf899ddd8ed68 recomputes to its tag."""
    blocks = _scenario_blocks(_FEATURE.read_text(encoding="utf-8"))
    assert _BEHAVIOR_1_HASH in blocks, (
        f"No scenario tagged @scenario_hash:{_BEHAVIOR_1_HASH} in {_FEATURE.name}"
    )
    recomputed = subprocess.run(
        ["scenarios", "hash"],
        input=blocks[_BEHAVIOR_1_HASH],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert recomputed == _BEHAVIOR_1_HASH, (
        f"scenario block recomputed to {recomputed!r} but the feature pins "
        f"@scenario_hash:{_BEHAVIOR_1_HASH}; re-tag or revert the edit"
    )


# ===========================================================================
# REAL fabro runtime SEMANTIC LAW + NEGATIVE CONTROL — the load-bearing proof.
# ===========================================================================

def test_fabro_runtime_max_retries_gives_multi_attempt_but_bare_retry_is_fail_fast(
    fabro_runtime,
):
    """Run the REAL fabro binary and prove the retry-and-survive semantic with
    the scenario's own negative control:

      * a node with ``max_retries=4`` emits max_attempts > 1 (=5) — the node
        RETRIES a failing attempt rather than terminating on the first, so a
        single transient error is not terminal;
      * a node with ONLY ``retry=4`` (the pre-fix attribute) emits
        max_attempts == 1 — it would have FAILED-FAST to the failsafe on the
        first 429, which is the lead-6ev8 regression.

    Teeth: if a future fabro made ``retry=`` effective (or ``max_retries=``
    inert) this proof — and the whole root-cause diagnosis — would change, and
    this test would surface it rather than let the def silently regress.
    """
    ma_effective = fabro_runtime.probe_max_attempts("max_retries=4")
    ma_bare_retry = fabro_runtime.probe_max_attempts("retry=4")
    assert ma_effective > 1, (
        "a node with max_retries=4 must yield max_attempts > 1 at the REAL "
        f"fabro runtime (retry-and-survive); got {ma_effective}"
    )
    assert ma_effective == 5, (
        "fabro v0.254.0 honors max_retries=N as max_attempts=N+1; got "
        f"{ma_effective} for max_retries=4"
    )
    assert ma_bare_retry == 1, (
        "NEGATIVE CONTROL: a node carrying ONLY `retry=4` (silently ignored by "
        "fabro) must yield max_attempts == 1 — the fail-fast posture that made "
        f"lead-6ev8 block on the first 429; got {ma_bare_retry}"
    )


def test_committed_classify_budget_yields_multi_attempt_at_real_runtime(
    fabro_runtime,
):
    """Bind the committed def to the proven runtime semantic: the ``classify``
    LLM node's declared retry budget, run through the REAL fabro binary, must
    yield max_attempts > 1 (retry-and-survive), not the fail-fast max_attempts=1
    that lead-6ev8 observed.

    Teeth: while classify carries only the inert ``retry=N`` (no ``max_retries``)
    the budget helper returns None and this REDs; once it carries an effective
    ``max_retries=N`` the real runtime yields max_attempts=N+1 > 1 and it GREENs.
    """
    body = _node_body(_workflow_text(), "classify")
    budget = _max_retries(body)
    assert budget is not None and budget >= 1, (
        "the committed `classify` LLM node must declare an EFFECTIVE "
        "`max_retries=N` (N>=1) retry budget — the inert `retry=N` alone leaves "
        f"it at max_attempts=1 (lead-6ev8 fail-fast). classify body:\n{body}"
    )
    ma = fabro_runtime.probe_max_attempts(f"max_retries={budget}")
    assert ma > 1, (
        "the committed classify retry budget must yield max_attempts > 1 at the "
        f"REAL fabro runtime; max_retries={budget} yielded max_attempts={ma}"
    )


# ===========================================================================
# Static teeth — every LLM/ACP node carries the EFFECTIVE max_retries budget.
# ===========================================================================

def test_llm_acp_nodes_carry_effective_max_retries_budget():
    """Every LLM/ACP agent node (`classify`, `suff`, `plan`, `impl`, `review`,
    `impl_f`) must carry ``max_retries=N`` (N>=1) — the attribute the REAL fabro
    runtime honors as max_attempts=N+1 — so no LLM node is left at the inert
    ``retry=`` / max_attempts=1 fail-fast posture that made lead-6ev8 block on
    the first transient 429.

    Teeth: leave any LLM/ACP node with only the inert `retry=N` (no
    `max_retries`) -> RED.
    """
    graph = _workflow_text()
    inert = []
    for name in _LLM_ACP_NODES:
        body = _node_body(graph, name)
        if not (_max_retries(body) or 0) >= 1:
            inert.append(name)
    assert not inert, (
        "these LLM/ACP nodes carry no EFFECTIVE `max_retries=N` budget, so the "
        "real fabro runtime leaves them at max_attempts=1 (fail-fast on the "
        f"first transient 429 — the lead-6ev8 regression): {inert!r}. Add "
        "`max_retries=N` (N>=1) to each."
    )
