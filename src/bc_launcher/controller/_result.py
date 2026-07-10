"""CommandResult value type returned by controller commands."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str = ""
