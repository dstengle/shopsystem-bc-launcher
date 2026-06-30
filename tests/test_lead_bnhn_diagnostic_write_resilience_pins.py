"""lead-bnhn — pin-recompute teeth for the launch-diagnostic write-resilience
scenarios.

The launch-diagnostic write must be best-effort/non-fatal (a write failure
must NOT abort the launch) and its DEFAULT target must be a user-writable
per-user state dir (NOT the root-owned /var/lib/bc-launcher). Those two
robustness properties are pinned by the BDD scenarios in
``features/bc_container_launch_diagnostic_write_resilience.feature`` (bound
through pytest-bdd elsewhere).

This module is the PIN teeth: it recomputes each scenario's block-only hash
with the canonical ``scenarios hash`` tool and asserts it matches the
``@scenario_hash`` tag embedded in the feature file. Editing a pinned
scenario block without re-tagging — or mis-tagging it — REDs these tests, so
the recorded hash genuinely pins the scenario text.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

FEATURE = (
    Path(__file__).resolve().parent.parent
    / "features"
    / "bc_container_launch_diagnostic_write_resilience.feature"
)

# The two robustness pins this BC authored for lead-bnhn.
NON_FATAL_HASH = "fe76a2f67262f665"
USER_WRITABLE_HASH = "aae4e5470f5c55cb"


def _scenario_blocks(text: str) -> dict[str, str]:
    """Map each ``@scenario_hash`` value to its block-only scenario text.

    The block is the ``Scenario:`` line through its last step — the SAME shape
    the existing 56-scenario hashes (0d010cf8f3175226, 7084bbbfdef94f81)
    recompute from: tag lines (``@scenario_hash:``, ``@bc:``) are NOT part of
    the hashed block.
    """
    lines = text.splitlines()
    blocks: dict[str, str] = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped_line = line.lstrip()
        # Only a genuine TAG line (starts with '@') carries a pin; a header
        # comment that merely MENTIONS @scenario_hash:... must not match.
        m = (
            re.search(r"@scenario_hash:([0-9a-f]+)", line)
            if stripped_line.startswith("@")
            else None
        )
        if m:
            tag_hash = m.group(1)
            # Advance to the Scenario: line (skip any further tag lines).
            j = i + 1
            while j < len(lines) and lines[j].lstrip().startswith("@"):
                j += 1
            assert lines[j].lstrip().startswith("Scenario"), (
                f"Expected a Scenario line after the hash tag; got {lines[j]!r}"
            )
            start = j
            j += 1
            # Consume until the next tag line, next Scenario, or EOF.
            while j < len(lines):
                stripped = lines[j].lstrip()
                if stripped.startswith("@") or stripped.startswith("Scenario"):
                    break
                j += 1
            # Trim trailing blank lines from the block.
            end = j
            while end > start + 1 and lines[end - 1].strip() == "":
                end -= 1
            blocks[tag_hash] = "\n".join(lines[start:end]) + "\n"
            i = j
            continue
        i += 1
    return blocks


def _recompute(block: str) -> str:
    """Recompute a block's hash via the canonical ``scenarios hash`` tool."""
    result = subprocess.run(
        ["scenarios", "hash"],
        input=block,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


pytestmark = pytest.mark.skipif(
    shutil.which("scenarios") is None,
    reason="canonical `scenarios` CLI not on PATH",
)


@pytest.mark.parametrize(
    "tag_hash",
    [NON_FATAL_HASH, USER_WRITABLE_HASH],
)
def test_scenario_block_recomputes_to_its_pin(tag_hash):
    """The block-only hash recomputes to the embedded @scenario_hash tag.

    Teeth: any edit to the pinned scenario text that is not reflected in the
    tag (or a wrong tag) makes the recompute diverge and REDs this test.
    """
    blocks = _scenario_blocks(FEATURE.read_text(encoding="utf-8"))
    assert tag_hash in blocks, (
        f"No scenario tagged @scenario_hash:{tag_hash} found in {FEATURE.name}"
    )
    recomputed = _recompute(blocks[tag_hash])
    assert recomputed == tag_hash, (
        f"Scenario block hash recomputed to {recomputed!r} but the feature "
        f"file pins it as @scenario_hash:{tag_hash}; re-tag or revert the edit"
    )
