"""Docker driver value types + errors.

Split from the former single-module bc_launcher/driver.py; re-exported via the
bc_launcher.driver package __init__ for import-path compatibility.
"""
from __future__ import annotations

from dataclasses import dataclass, field




# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class DockerSocketUnreachableError(Exception):
    """Raised when the Docker socket / daemon cannot be reached.

    lead-pixf (010e776c).  A docker CLI call that fails because the daemon
    socket is unreachable (e.g. ``Cannot connect to the Docker daemon at
    unix:///var/run/docker.sock``) is an INFRASTRUCTURE failure, not an
    empty result.  ``list_bc_containers`` raises this instead of returning
    an empty list so the controller can exit non-zero with a diagnostic
    naming the socket, rather than masking the outage as "No BC containers
    found".
    """



# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class ContainerMount:
    """Represents one mount entry for a running container."""
    type: str          # "bind", "volume", "tmpfs", …
    source: str        # host-side source path / volume name / socket path
    destination: str   # path inside the container



@dataclass
class ContainerInfo:
    """Aggregated state for a BC container."""
    name: str
    running: bool
    mounts: list[ContainerMount] = field(default_factory=list)

