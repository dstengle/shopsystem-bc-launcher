"""Host state-dir resolution + launch-failure diagnostic file path/markers.

Extracted verbatim from ``controller`` (Phase 1 of the controller.py
decomposition). Leaf module; re-exported by ``controller`` for import-path
compatibility. Do not import ``controller`` from here (would cycle).
"""
from __future__ import annotations

import os
from pathlib import Path

from bc_launcher.naming import _container_name


# ---------------------------------------------------------------------------
# Launch-failure diagnostic file (lead-63em — re-issue of lead-2qta)
# ---------------------------------------------------------------------------
#
# When a launch fails to bring up a USABLE agent session, the operator needs
# to learn WHY from the HOST, without attaching into any tmux session and
# without relying on the launch command's stderr (ephemeral) or the
# bc-container monitor tmux pane (needs a live session that never came up).
# The launcher therefore writes a PERSISTED diagnostic FILE on the same
# host-visible per-BC surface the mailbox is read from.
#
# DOCUMENTED per-BC host-discoverable location (lead-63em RESOLUTION of the
# lead-2qta surface-ambiguity clarify):
#
#   <BCLAUNCHER_HOST_STATE_DIR>/<container-name>/launch-diagnostic.txt
#
# where the per-BC state root is the launcher host directory the operator's
# per-BC mailbox/state is read from.  It is resolved from the
# ``BCLAUNCHER_HOST_STATE_DIR`` env var when set, else defaults to a
# per-USER state directory (``$XDG_STATE_HOME/bc-launcher``, falling back to
# ``~/.local/state/bc-launcher``).  Each BC owns a per-BC subdirectory named
# for its container (``bc-<bc_name>``), exactly the per-BC layout shape the
# launcher already uses for the container identity surface, so the
# diagnostic file lands on the SAME per-BC surface and is host-discoverable
# at a single, documented, predictable path.  The launcher creates the
# directory tree on demand, so the surface exists even on the very first
# failed launch (when no container directory had been created yet).
#
# lead-bnhn (P1 bugfix): the default state root was ``/var/lib/bc-launcher``,
# which is root-owned and NOT writable by the invoking (shop-shell) user, so
# the on-demand ``mkdir(parents=True)`` in the diagnostic write raised
# PermissionError and ABORTED the very launch the diagnostic was supposed to
# describe.  The default is now a per-USER state directory the invoking user
# can always write to, so the documented host-discoverable surface no longer
# REQUIRES a root-pre-created path.  (The diagnostic write is ALSO wrapped
# best-effort / non-fatal at the controller call site — see
# ``_write_launch_diagnostic`` — so even an unwritable override location can
# never abort the launch.)
#
# The file is a single human-readable line carrying the literal cause-marker
# token (so an operator / tool can grep for the cause) followed by a
# human-readable reason describing why the session failed to come up.
BCLAUNCHER_HOST_STATE_DIR_ENV = "BCLAUNCHER_HOST_STATE_DIR"

XDG_STATE_HOME_ENV = "XDG_STATE_HOME"

# Per-USER default state-dir LEAF, joined under $XDG_STATE_HOME (or
# ~/.local/state when XDG_STATE_HOME is unset) — a location writable by the
# invoking user, never the root-owned /var/lib (lead-bnhn).
DEFAULT_HOST_STATE_DIR_LEAF = "bc-launcher"

LAUNCH_DIAGNOSTIC_FILENAME = "launch-diagnostic.txt"



def default_host_state_dir() -> Path:
    """The per-USER default launch-diagnostic state root (lead-bnhn).

    Resolves to ``$XDG_STATE_HOME/bc-launcher`` when ``XDG_STATE_HOME`` is set,
    else ``~/.local/state/bc-launcher`` (the XDG Base Directory default for
    per-user state).  This is a location the INVOKING (shop-shell) user can
    write to, so the on-demand parent ``mkdir`` in the diagnostic write does
    NOT require a root-pre-created ``/var/lib/bc-launcher`` and cannot raise
    PermissionError on a fresh-adopter bootstrap.  Used ONLY when
    ``BCLAUNCHER_HOST_STATE_DIR`` is unset; an explicit override still wins.
    """
    xdg = os.environ.get(XDG_STATE_HOME_ENV)
    if xdg:
        base = Path(xdg)
    else:
        base = Path.home() / ".local" / "state"
    return base / DEFAULT_HOST_STATE_DIR_LEAF


# The four documented launch-failure cause-marker tokens.  Each is the
# literal token written into the diagnostic file's ``cause:`` field so the
# operator is pointed at the right repair.
CAUSE_MARKER_MESSAGING_DB = "messaging-db"

CAUSE_MARKER_AGENT_VAULT = "agent-vault"

CAUSE_MARKER_READINESS = "readiness"

CAUSE_MARKER_AGENT_STARTUP = "agent-startup"



def launch_diagnostic_path(bc_name: str) -> Path:
    """Documented per-BC host-discoverable launch-diagnostic file path.

    lead-63em.  Returns the absolute host path at which a failed launch's
    persisted diagnostic file lives for ``bc_name``:

        <state-root>/<container-name>/launch-diagnostic.txt

    The state root is ``BCLAUNCHER_HOST_STATE_DIR`` when set, else the
    per-USER ``default_host_state_dir()`` (``$XDG_STATE_HOME/bc-launcher``,
    default ``~/.local/state/bc-launcher`` — lead-bnhn: writable by the
    invoking user, never the root-owned ``/var/lib/bc-launcher``).  The
    per-BC subdirectory is the container name (``bc-<bc_name>``), matching
    the launcher's existing per-BC identity shape.  This is the SAME
    host-visible per-BC surface the operator's per-BC mailbox/state is read
    from — readable from the host with NO tmux attach and independent of the
    launch command's stderr.
    """
    override = os.environ.get(BCLAUNCHER_HOST_STATE_DIR_ENV)
    root = Path(override) if override else default_host_state_dir()
    return root / _container_name(bc_name) / LAUNCH_DIAGNOSTIC_FILENAME



def _resolve_host_path(devcontainer_path: Path) -> Path:
    """
    If running inside a devcontainer where ``devcontainer_path`` lies on a bind
    mount, return the corresponding host-visible source path.  Falls back to
    ``devcontainer_path`` if no covering bind mount is found (i.e., not inside
    a bind-mounted devcontainer).

    Needed because mount sources passed to ``docker run`` are interpreted by
    the host docker daemon — bind-mount sources like ``/home/vscode/.claude``
    that are valid inside the launching container may not exist on the host.

    Resolution order:
      1. If ``BCLAUNCHER_HOST_HOME`` env var is set and the path is under the
         current ``Path.home()``, substitute the env var for the home prefix.
         This handles devcontainers whose home is bind-mounted from a host
         user home that we know explicitly.
      2. Otherwise walk ``/proc/self/mountinfo`` for the longest mount-point
         prefix that covers the path, and substitute the source root.
      3. Otherwise return the path unchanged.
    """
    try:
        target = str(devcontainer_path.resolve())
    except OSError:
        target = str(devcontainer_path)
    host_home = os.environ.get("BCLAUNCHER_HOST_HOME")
    if host_home:
        home_str = str(Path.home())
        if target == home_str:
            return Path(host_home)
        if target.startswith(home_str + "/"):
            return Path(host_home + target[len(home_str):])
    best_mount_point: str | None = None
    best_source_root: str | None = None
    try:
        with open("/proc/self/mountinfo", encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 5:
                    continue
                source_root = parts[3]
                mount_point = parts[4]
                if target == mount_point or target.startswith(mount_point + "/"):
                    if best_mount_point is None or len(mount_point) > len(best_mount_point):
                        best_mount_point = mount_point
                        best_source_root = source_root
    except OSError:
        return devcontainer_path
    if best_mount_point is None or best_source_root is None:
        return devcontainer_path
    if target == best_mount_point:
        resolved = best_source_root
    else:
        suffix = target[len(best_mount_point):]
        resolved = best_source_root + suffix
    # mountinfo source roots may be dataset-relative (start with "/<user>/...")
    # rather than absolute host paths.  When BCLAUNCHER_HOST_HOME is set, apply
    # the same home-prefix substitution to the mountinfo result so it lands at
    # an absolute host path.
    if host_home:
        user_leaf = "/" + Path(host_home).name
        if resolved == user_leaf:
            return Path(host_home)
        if resolved.startswith(user_leaf + "/"):
            return Path(host_home + resolved[len(user_leaf):])
    return Path(resolved)
