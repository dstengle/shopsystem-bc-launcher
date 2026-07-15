# Runbook: keeping a review venv's import resolution honest

Applies to **any** shop that verifies a commit by provisioning a throwaway venv
and running the test suite in it. Nothing here is specific to one package — the
traps are structural properties of editable installs, shared containers, and
`.pth` path configuration, so the same walls are waiting in every BC that does
differential verification.

The failure mode this runbook exists to prevent is the worst one a review can
have: **a confidently-reported ruling that is false**, because the tests never
imported the code the reviewer thought they were testing.

Throughout, `<pkg>` is the package under test (in this shop, `bc_launcher`),
`<repo>` is the checkout under test, and `<venv>` is the review venv.

## The standing prescription — and its limit

The baseline prescription is:

> Build an isolated venv and assert `<pkg>.__file__` resolves to the checkout
> under test.

This is **necessary but not sufficient**. It is necessary because every trap
below manifests as a wrong `__file__`. It is not sufficient because asserting
resolution in one process does not constrain resolution in the *next* process
(trap 4), and because a passing assert on one arm of a differential experiment
says nothing about the other arm.

Treat the assert as a **detector**, not a guarantee. The guarantee comes from
purging path config and asserting in-process, per trap 4's mitigation.

## Trap 1 — the stale global editable install (shared environment skew)

**Symptom.** Tests import `<pkg>` from a sibling or deleted worktree rather
than from your own checkout, producing false reds and false greens. Observed:
an agent's `import` resolving to `.../.worktrees/<other-work-id>/src/<pkg>`
while the agent believed it was testing its own branch.

**Why.** Parallel worktree dispatches share ONE Python environment. A global
`pip install -e <path>` repoints the import for **every** concurrently-running
process to whichever path last ran it. It is last-writer-wins, process-wide,
and silent.

**Mitigation.** Verify in an isolated checkout whose import resolves to that
same checkout's `src`. Do NOT verify via `git archive` extraction — it strips
`.git`, so any test shelling out to git (e.g. `git ls-files --error-unmatch`)
false-fails with "not a git repository". Use `git worktree add --detach`.

## Trap 2 — a `.pth` pointing at a deleted worktree

**Symptom.** Import succeeds but resolves to a path that no longer exists, or
silently falls back to a stale copy; a reused scratch venv (e.g. `/tmp/venv85`)
carries a `.pth` written by a previous, since-pruned worktree.

**Why.** `.pth` files are path config, not dependencies. Deleting the worktree
they name does not remove or invalidate them; nothing garbage-collects them.
Reusing a scratch venv across sessions therefore inherits stale path entries.

**Mitigation.** Never reuse a review venv across dispatches. Provision a fresh
venv at a **unique, work-id-scoped path** — do not reuse a fixed path such as
`/tmp/v` or `/tmp/venv85`, which invites cross-agent collision (see trap 4's
root cause). Purge inherited path config before trusting the venv.

## Trap 3 — the pytest shim's `python -E` strips PYTHONPATH

**Symptom.** You export `PYTHONPATH=<repo>/src` to force resolution, the export
is correct, and the import *still* resolves elsewhere.

**Why.** `python -E` tells the interpreter to ignore `PYTHON*` environment
variables. A pytest shim invoking the interpreter with `-E` discards your
`PYTHONPATH` before the first line of test code runs. The override is not
overridden — it is never read.

**Mitigation.** Do not rely on `PYTHONPATH` alone as the forcing mechanism when
a shim may re-exec the interpreter. Assert the resolved path rather than
assuming the export took, and prefer purging foreign `.pth` files (trap 4) over
trying to out-rank them with environment variables.

## Trap 4 — a foreign editable install lands *inside* your isolated venv

This is the trap the other three do not cover: the venv is correct when you
check it, and wrong when you use it.

**Symptom.** A freshly-provisioned, **verified-clean** review venv:

- loses its target package — `pytest` was confirmed present (e.g. `pip list`
  showing pytest 9.1.1) and is simply **gone** moments later; and
- gains a **foreign** `__editable__.<pkg>-<ver>.pth` naming `/workspace/src`
  that no one in the review provisioned,

all within **~1 second** of provisioning. Observed: verified clean at
`20:26:04.099`, clobbered by `20:26:04.537` — a ~440ms window.

**Why the existing "assert `__file__`" prescription is insufficient.** It is
necessary but not sufficient, for two independent reasons:

1. **TOCTOU.** The assert-then-run pattern is a cross-process,
   time-of-check-to-time-of-use split. The assert passes in process A; the
   tests run later in process B; the clobber fits entirely inside the window
   between them. Nothing carries the assert's guarantee across the process
   boundary — the check has expired by the time it matters.

2. **It passes on the arm you are watching.** In a differential experiment the
   assert **PASSES for the POST/target arm even while the PRE/control arm is
   silently poisoned to the same resolution.** Both arms then import the same
   code, the RED/GREEN contrast collapses into a same-code comparison, and the
   experiment reports a confident FALSE ruling — precisely on the claim most
   worth attacking. A green assert on one arm is not evidence about the other.

**Root cause (established for this shop; see the "Attribution" section).** The
clobber does not require a hostile or exotic process. A bare
`pip install -e /workspace` **retargets into whatever venv `VIRTUAL_ENV` names**,
because pip honours `VIRTUAL_ENV` from the ambient environment. An agent
following a standing "repoint the global install back with
`pip install -e /workspace`" instruction, running in a shell that inherited a
`VIRTUAL_ENV` pointing at someone else's review venv, writes
`__editable__.<pkg>-<ver>.pth` containing `/workspace/src` **into that venv** —
believing it touched only the global environment. Reproduced deterministically;
see Attribution below.

**Mitigation — all three steps. Do not adopt only the first.**

1. **Purge foreign path config, twice.** After provisioning any review venv:

   ```bash
   rm -f <venv>/lib/python*/site-packages/__editable__*.pth
   ```

   and **re-purge immediately before each run** — the clobber **recurs**, so a
   single purge at provisioning time is not enough. Purging is idempotent and
   costs nothing; treat it as part of every invocation, not part of setup.

2. **Assert import resolution in the SAME process that runs the tests.** Not a
   separate assert-then-run step — that is exactly the TOCTOU split above. Fold
   the assert into the test process itself, so the check and the use share one
   process lifetime and no window exists between them. For example, as a
   `conftest.py` collection-time assert, or:

   ```bash
   <venv>/bin/python -c "
   import <pkg>, pathlib, sys
   resolved = pathlib.Path(<pkg>.__file__).resolve()
   expected = pathlib.Path('<repo>/src/<pkg>/__init__.py').resolve()
   assert resolved == expected, f'RESOLUTION SKEW: {resolved} != {expected}'
   print('resolved:', resolved)
   import pytest; sys.exit(pytest.main([...]))
   "
   ```

3. **For any differential experiment, assert resolution independently on EVERY
   arm, and print the resolved path per arm.** Never infer the control arm's
   resolution from the target arm's passing assert. Printing the resolved path
   for each arm is what makes a **poisoned control arm visible rather than
   silent** — an unprinted arm is an unverified arm, and an unverified control
   arm can invert your ruling without ever failing a check.

**Validation.** This mitigation was exercised on the dispatch that surfaced the
trap: with the purge + in-process assert + per-arm resolution printing in place,
`pre + mutation` ran 23 passed GREEN and `post + mutation` produced 1 failure —
the correct, non-collapsed contrast.

## Attribution — who clobbers the venv

Do not assume an external agent. In a shared container the likeliest cause is
**a role in your own shop following standing guidance**:

- Subagents run **in-process** with their parent. A subagent's
  `pip install -e ...` leaves **no separate entry in the process table**, which
  is why the mutation reads as "unattributed" — there may be no external
  process at all. Absence of a suspicious process is not evidence of an
  external cause.
- Any standing instruction of the form "repoint the global install back with
  `pip install -e <path>`" is a loaded gun in a shared container: it is correct
  in intent and silently venv-scoped in effect whenever `VIRTUAL_ENV` is set.

**Reproduction** (deterministic; confirms pip's `VIRTUAL_ENV` retargeting):

```bash
python3 -m venv /tmp/probe_v --system-site-packages
VIRTUAL_ENV=/tmp/probe_v PATH=/tmp/probe_v/bin:$PATH pip install -e /workspace
cat /tmp/probe_v/lib/python*/site-packages/__editable__.*.pth   # -> /workspace/src
```

A bare `pip install -e /workspace`, with no venv named on the command line,
writes the `.pth` **inside** `/tmp/probe_v`.

**Note — the two halves of the symptom have different causes.** The foreign
`.pth` is fully explained by the `VIRTUAL_ENV` retargeting above. The
**disappearance of pytest is not**: `pip install -e` does not uninstall pytest
(verified — pytest 9.1.1 survives the retargeted install). A vanished pytest
implies the venv was **recreated** (`python -m venv` over an existing path wipes
`site-packages`; pytest is not in this image's global `site-packages`, so a
`--system-site-packages` venv does not get it back). That points at **two agents
colliding on the same hardcoded venv path**. Hence trap 2's rule: provision
review venvs at unique, work-id-scoped paths, never at a fixed `/tmp/v`.

## Checklist

Before trusting any review venv result:

- [ ] Venv provisioned at a **unique**, work-id-scoped path (not a fixed one).
- [ ] `__editable__*.pth` purged after provisioning **and** before each run.
- [ ] Import resolution asserted **in the same process** that runs the tests.
- [ ] Every arm of a differential experiment asserts **and prints** its resolved
      path.
- [ ] The printed paths were actually **read**, not just emitted.
