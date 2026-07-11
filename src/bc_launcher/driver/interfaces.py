"""DockerDriver / RegistryDriver structural protocols.

Split from the former single-module bc_launcher/driver.py; re-exported via the
bc_launcher.driver package __init__ for import-path compatibility.
"""
from __future__ import annotations

from typing import Protocol

from bc_launcher.driver._types import ContainerInfo, ContainerMount




# ---------------------------------------------------------------------------
# Registry driver protocol (scenario af2f03d3ac519cb5)
# ---------------------------------------------------------------------------

class RegistryDriver(Protocol):
    """Resolve a registry image reference to its current digest.

    The seam scenario 39 (af2f03d3ac519cb5) pins: launch must resolve the
    bc-base ``latest`` tag against the registry BEFORE starting the container,
    so a republished image (a newer digest under the same ``latest`` tag)
    reaches the new container instead of a stale locally-cached digest.

    This protocol belongs to scenario 39 ONLY (the launch-path / behavioral
    side).  It is explicitly NOT shared with the CI/workflow scenarios
    37/38/41, which are pinned declaratively as committed YAML with no in-src
    seam (per the architect ruling on lead-yk3o).
    """

    def resolve_digest(self, image_ref: str) -> str:
        """Resolve ``image_ref`` (e.g. ``ghcr.io/...:latest``) to a digest.

        Returns the registry-current digest for the tag named in
        ``image_ref``.  The real implementation queries the registry; the
        test fake returns the digest the test has configured as the
        registry-current ``latest``.
        """
        ...



# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

class DockerDriver(Protocol):
    """Minimal Docker operations needed by bc_launcher."""

    def is_running(self, container_name: str) -> bool:
        """Return True if the named container is currently running."""
        ...

    def run(
        self,
        container_name: str,
        image: str,
        env: dict[str, str],
        mounts: list[tuple[str, str, str, bool]],  # (type, source, dest, readonly)
        network: str | None,
        detach: bool,
        group_add: list[str] | None = None,
    ) -> None:
        """Start a new container.

        ``group_add`` carries supplementary group ids (or names) to grant the
        launched container's process (``docker run --group-add <gid>``).  This
        is REQUIRED for the opt-in docker-socket mount (lead-wdvx): bind-mounting
        ``/var/run/docker.sock`` alone leaves the container's non-root default
        user OUTSIDE the host socket's owning group, so every docker call inside
        the container is rejected permission-denied.  Adding the host socket's
        gid to the container's supplementary groups is what makes the mounted
        socket actually usable.  None / empty adds no ``--group-add`` (the
        default, so a launch WITHOUT the docker-socket flag grants no group).
        """
        ...

    def pull(self, image_ref: str) -> None:
        """Pull ``image_ref`` from the registry into the local Docker cache.

        Scenario af2f03d3ac519cb5 (freshness at launch): launch resolves the
        bc-base ``latest`` tag's CURRENT registry digest (``D_new``) and then
        pulls THAT digest before starting the container, so the local cache is
        populated with the republished content rather than serving whatever
        digest (``D_old``) the cache already holds under the moving ``latest``
        tag.  The real implementation shells out to ``docker pull <image_ref>``;
        the test fake fetches the registry-current digest into its in-memory
        local cache so a run of the digest-pinned reference serves ``D_new``.
        """
        ...

    def exec_run(
        self,
        container_name: str,
        command: list[str],
        user: str | None = None,
        env: dict[str, str] | None = None,
        detach: bool = False,
        input: str | None = None,
    ) -> subprocess.CompletedProcess:
        """Execute a command inside a running container.

        If ``input`` is provided it is streamed to the exec's STDIN
        (``docker exec -i``) instead of being carried on the argv.  This is
        REQUIRED for placing a large content blob (the fabro def-bundle, or an
        oversized startup prompt) WITHOUT tripping the Linux MAX_ARG_STRLEN
        128 KiB per-single-argument limit — a blob carried as one argv element
        fails the spawn with E2BIG ("Argument list too long") even when the
        total env is tiny, so the blob must leave the argv entirely (lead-m4zt).
        ``detach=True`` issues ``docker exec -d`` (background, returns at once).

        If ``user`` is provided, run the command as that container user
        (``docker exec -u <user>``).  This is required for tmux client
        operations against an agent session owned by a non-root user:
        tmux refuses cross-user attach, so every send-keys / capture-pane
        / has-session call against a vscode-owned tmux session must also
        run as vscode.

        If ``env`` is provided, each key=value pair is injected into the
        exec's environment (``docker exec -e KEY=VALUE``).  This is REQUIRED
        for the launch-time auto-clone (bclaunch-5fji): the clone runs in a
        non-login shell that does NOT source ``/etc/profile.d/agent-vault-ca.sh``,
        so it inherits neither ``GIT_SSL_CAINFO`` nor a usable proxy.  The
        controller passes the broker MITM ``HTTPS_PROXY`` and ``GIT_SSL_CAINFO``
        explicitly on the clone exec so the brokered clone reaches the broker
        and trusts its CA without depending on a login shell.
        """
        ...

    def exec_interactive(
        self,
        container_name: str,
        command: list[str],
        user: str | None = None,
    ) -> None:
        """Execute an interactive command inside a running container (replaces process).

        ``user`` has the same semantics as in ``exec_run``: required for
        ``tmux attach-session`` against an agent session owned by a
        non-root container user.
        """
        ...

    def stop(self, container_name: str) -> None:
        """Stop and remove the named container."""
        ...

    def host_socket_gid(self, socket_path: str) -> int | None:
        """Return the owning group id of the host docker socket, or None.

        lead-wdvx (Bug 1).  When the opt-in docker-socket mount is enabled the
        launcher must grant the launched container supplementary-group access
        to the mounted socket, so it needs the HOST socket's actual gid at run
        time (it varies by host — the scenarios model gid 984).  This resolves
        it by stat-ing ``socket_path``; None when the path is absent / cannot
        be stat-ed (so the launcher adds no group rather than crashing).
        """
        ...

    def list_bc_containers(self) -> list[ContainerInfo]:
        """Return ContainerInfo for every container whose name starts with 'bc-'."""
        ...

    def get_mounts(self, container_name: str) -> list[ContainerMount]:
        """Return the mount list for a running container."""
        ...

    def agent_online(self, container_name: str) -> bool:
        """Return True when the container's "agent" tmux session holds a LIVE
        claude process whose ``shop-msg watch`` inbox watcher is armed.

        lead-pixf (f2ddd6c7 / aeebb281).  Agent PRESENCE — distinct from the
        tmux session merely existing.  The session can be "active" (present)
        while the agent is offline (e.g. left at a bash prompt, or claude
        exited).  An agent is ONLINE only when all three hold inside the
        container: the "agent" tmux session exists, a live ``claude`` process
        is running, and that agent has armed its ``shop-msg watch`` inbox
        watcher (the surface through which dispatched work reaches the BC).
        Used by ``status`` (to report presence) and by ``start-agent`` (to
        no-op when an agent is already live instead of starting a second one
        / hanging on the readiness probe).
        """
        ...

    def last_command(self) -> list[str]:
        """Return the most recently built shell command (for test assertions)."""
        ...

    def network_exists(self, network_name: str) -> bool:
        """Return True if the named Docker network exists."""
        ...

    def network_create(self, network_name: str) -> None:
        """Create a Docker network with the given name."""
        ...

    def wait_for_pane_marker(
        self,
        container_name: str,
        tmux_session: str,
        marker: str,
        timeout_seconds: float,
        poll_interval_seconds: float = 0.5,
        _clock=None,
        _capture=None,
    ) -> bool:
        """
        Poll ``tmux capture-pane`` for the named session until ``marker`` is
        observed in the pane contents.

        lead-j351 — marker-keyed (progress-based) wait.  ``timeout_seconds``
        is a *no-progress / idle* budget rather than an absolute wall-clock
        cap: while the pane keeps changing (the boot is still progressing
        toward readiness) the wait keeps polling, even past the legacy 60s
        deadline, so a slow brokered boot still has its marker observed.  The
        wait abandons (returns False) only after ``timeout_seconds`` of NO
        progress with the marker still absent.

        Returns True if the marker was observed, False on the no-progress
        timeout.  The controller uses this to sequence Claude Code startup
        inside the tmux session (claude command → ready banner → trust accept
        → input ready) before injecting the first user prompt.

        ``_clock`` and ``_capture`` are injectable test seams; production
        passes neither.
        """
        ...

    def capture_pane(
        self, container_name: str, tmux_session: str
    ) -> str:
        """Return the current rendered contents of a tmux session's pane.

        lead-q3uy — used by the engage path to inspect, at a single instant,
        whether the in-container agent runtime is presenting a blocking
        interactive option screen (and whether that screen advertises an
        Escape/dismiss affordance) AFTER the input-ready marker but BEFORE the
        startup prompt is submitted.  This is a ONE-SHOT capture (distinct from
        ``wait_for_pane_marker``, which polls until a marker appears): the
        launcher reads the rendered screen so it can both classify it and, when
        it auto-dismisses an escape-able screen, log the captured content as a
        host-discoverable WARNING.  Same ``capture-pane`` surface
        ``bc-container monitor`` reads, so the launcher needs no in-container
        attach.
        """
        ...

    def messaging_db_reachable(
        self, dsn: str, container: str | None = None
    ) -> bool:
        """Return True if the messaging database at ``dsn`` is reachable.

        This is the readiness barrier the launcher checks before injecting
        any startup prompt: a BC agent whose messaging backend is
        unreachable cannot arm its inbox watcher or drain pending inbox, so
        engaging it is pointless.  The controller calls this BEFORE prompt
        injection; on failure it surfaces a readiness error naming the DSN
        and does NOT send the startup prompt.

        lead-cs7k: when ``container`` is supplied the probe runs from inside
        the launched container's network context (``docker exec``) so its
        reachability matches the container's, not the launcher host's.
        """
        ...

    def health_status(self, container_name: str) -> str:
        """Return the container's reported health status string.

        One of ``"healthy"``, ``"unhealthy"``, ``"starting"``, or
        ``"none"`` (no healthcheck configured), mirroring
        ``docker inspect --format '{{.State.Health.Status}}'``.
        """
        ...

    def agent_vault_reachable(
        self, broker_address: str, container: str | None = None
    ) -> bool:
        """Return True if the agent-vault broker at ``broker_address`` is reachable.

        Under the agent-vault credential model (ADR-026) the launcher mounts
        NO host credential into the container; the agent's Claude OAuth and
        GitHub credentials are substituted on outbound requests by an
        agent-vault broker reached through an HTTPS proxy on the shopsystem
        network.  An agent whose broker is unreachable can authenticate to
        nothing, so this is a launch-time readiness barrier alongside the
        messaging-database check: the controller calls this BEFORE engaging
        the agent and surfaces a readiness failure (naming the broker
        address) when it returns False.

        lead-cs7k: when ``container`` is supplied the probe runs from inside
        the launched container's network context (``docker exec``).
        """
        ...

    def write_launch_diagnostic(
        self, host_path: str, content: str
    ) -> None:
        """Persist a launch-failure diagnostic to a host-visible file.

        lead-63em.  When a launch fails to bring up a usable agent session
        (any of the four documented causes — messaging-db, agent-vault,
        readiness, agent-startup), the controller writes a PERSISTED
        diagnostic file at the documented per-BC host-discoverable location
        (``host_path``) on the SAME host-visible per-BC surface the mailbox is
        read from.  The file is readable from the host WITHOUT attaching into
        any tmux session and WITHOUT relying on the launch command's stderr or
        the bc-container monitor tmux pane: it survives the launch process
        exiting.  ``content`` carries the literal cause-marker token and a
        human-readable reason.  The implementation creates any missing parent
        directories so the per-BC surface exists even on the very first
        failed launch.
        """
        ...

