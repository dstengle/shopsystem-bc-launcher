"""
FakeGitDriver — in-memory test double for GitDriver.

Records clone calls and returns pre-configured remote URLs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CloneCall:
    remote_url: str
    dest: Path


class FakeGitDriver:
    """
    In-memory GitDriver for tests.

    - clone() records the call and creates the dest directory (simulating a clone).
    - get_remote_url() returns whatever was configured via set_remote_url().
    """

    def __init__(self) -> None:
        self.clone_calls: list[CloneCall] = []
        # Maps repo path -> configured remote URL
        self._remotes: dict[str, str] = {}

    def set_remote_url(self, repo_path: Path, url: str) -> None:
        """Pre-configure what get_remote_url() will return for this path."""
        self._remotes[str(repo_path)] = url

    def clone(self, remote_url: str, dest: Path) -> None:
        """Record the clone and create the directory."""
        self.clone_calls.append(CloneCall(remote_url=remote_url, dest=dest))
        dest.mkdir(parents=True, exist_ok=True)
        # Record the remote for subsequent get_remote_url() calls
        self._remotes[str(dest)] = remote_url

    def get_remote_url(self, repo_path: Path) -> str | None:
        """Return the configured remote URL, or None if not set."""
        return self._remotes.get(str(repo_path))
