"""Internal docker-CLI stderr/port parsing helpers.

Split from bc_launcher/driver/real.py; re-exported via bc_launcher.driver.
"""
from __future__ import annotations






# ---------------------------------------------------------------------------
# Real implementation (shells out to docker CLI)
# ---------------------------------------------------------------------------

def _is_docker_socket_unreachable(stderr: str) -> bool:
    """Classify a docker CLI stderr as a daemon-socket-unreachable failure.

    lead-pixf (010e776c).  The docker CLI emits a recognizable message when
    it cannot reach the daemon socket — e.g. "Cannot connect to the Docker
    daemon at unix:///var/run/docker.sock. Is the docker daemon running?".
    Matching on that signature lets ``list_bc_containers`` distinguish an
    infrastructure outage (raise) from an ordinary empty result (return []).

    lead-wdvx (Bug 2).  The daemon-DOWN signatures above are NOT the only way
    docker becomes unusable: a CONFIGURATION fault — the socket mounted but the
    calling user denied access (permission-denied), or the socket not mounted
    into the calling environment at all — also makes every docker call fail,
    and the product authority asked the error handling cover "any future
    configuration problems", not just daemon-down.  Those faults must ALSO be
    classified as docker-unreachable so the docker-dependent subcommands exit
    non-zero and NAME the cause, rather than falling through to the ordinary
    empty-result path ("No BC containers found.", exit 0) and MASKING the real
    fault.  The docker CLI emits recognizable signatures for these too:
      * permission-denied:  "permission denied while trying to connect to the
        Docker daemon socket at unix:///var/run/docker.sock: ... connect:
        permission denied"
      * not-mounted:        "no such file or directory" against the socket path
        (the unix socket is absent from the calling environment).
    """
    text = (stderr or "").lower()
    return (
        "cannot connect to the docker daemon" in text
        or "is the docker daemon running" in text
        or ("docker" in text and "daemon" in text and "socket" in text)
        # lead-wdvx: permission-denied to the mounted socket.
        or "permission denied" in text
        # lead-wdvx: the socket is not mounted into the calling environment
        # ("no such file or directory" against the docker socket path).
        or (
            "no such file or directory" in text
            and ("docker.sock" in text or "/var/run/docker" in text)
        )
    )



def _parse_host_port(
    addr: str, default_port: int | None = None
) -> tuple[str | None, int | None]:
    """Parse a ``host`` / ``port`` out of a DSN or broker address.

    Accepts a bare ``host:port`` or a scheme-qualified URL.  Returns
    ``(host, port)`` with ``port`` falling back to ``default_port`` when the
    address omits one.  ``(None, None)`` when no host can be parsed.
    """
    if not addr:
        return None, None
    from urllib.parse import urlparse
    s = addr.strip()
    try:
        parsed = urlparse(s if "://" in s else "tcp://" + s)
    except ValueError:
        return None, None
    host = parsed.hostname
    if not host:
        return None, None
    try:
        port = parsed.port or default_port
    except ValueError:
        port = default_port
    return host, port
