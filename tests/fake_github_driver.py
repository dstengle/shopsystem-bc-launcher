"""
FakeGitHubDriver — in-memory test double for GitHubDriver.

Pre-configure which URLs are reachable or unreachable before running the
controller under test.
"""
from __future__ import annotations


class FakeGitHubDriver:
    """
    In-memory GitHubDriver for tests.

    By default all URLs are reachable.  Configure exceptions before calling
    the controller.
    """

    def __init__(self) -> None:
        # URLs explicitly marked as unreachable; all others are reachable
        self._unreachable: set[str] = set()
        # If True, all URLs are unreachable (except those explicitly marked reachable)
        self._all_unreachable: bool = False
        self._reachable_exceptions: set[str] = set()

    def set_unreachable(self, url: str) -> None:
        """Mark a specific URL as unreachable."""
        self._unreachable.add(url)

    def set_all_unreachable(self) -> None:
        """Mark all URLs as unreachable."""
        self._all_unreachable = True

    def set_reachable(self, url: str) -> None:
        """Mark a specific URL as reachable (used with set_all_unreachable)."""
        self._unreachable.discard(url)
        self._reachable_exceptions.add(url)

    def is_reachable(self, url: str) -> bool:
        if self._all_unreachable:
            return url in self._reachable_exceptions
        return url not in self._unreachable
