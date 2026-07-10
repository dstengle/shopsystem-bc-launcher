"""Real docker-CLI + registry driver implementations.

Split from the former single-module bc_launcher/driver.py; re-exported via the
bc_launcher.driver package __init__ for import-path compatibility.
"""
from __future__ import annotations

import subprocess
import time

from bc_launcher.driver._types import (
    ContainerInfo,
    ContainerMount,
    DockerSocketUnreachableError,
)
from bc_launcher.driver._util import _is_docker_socket_unreachable, _parse_host_port


class RealDockerDriver:
    """Production DockerDriver that shells out to the docker CLI."""

    def __init__(self) -> None:
        self._last_command: list[str] = []

    def is_running(self, container_name: str) -> bool:
        result = subprocess.run(
            ["docker", "ps", "--filter", f"name=^{container_name}$",
             "--format", "{{.Names}}"],
            capture_output=True, text=True, check=False,
        )
        # lead-wdvx (Bug 2): a docker call that fails because the daemon socket
        # is unreachable — daemon down, OR a CONFIG fault (socket mounted but
        # permission-denied, or not mounted at all) — is an infrastructure
        # failure, NOT "the container is not running".  Raise so callers like
        # ``status`` surface it as a non-zero, cause-naming diagnostic rather
        # than reporting a (false) "stopped" state that is indistinguishable
        # from a legitimately-absent container.
        if result.returncode != 0 and _is_docker_socket_unreachable(
            result.stderr
        ):
            raise DockerSocketUnreachableError(result.stderr.strip())
        return container_name in result.stdout.split()

    def run(
        self,
        container_name: str,
        image: str,
        env: dict[str, str],
        mounts: list[tuple[str, str, str, bool]],
        network: str | None,
        detach: bool,
        group_add: list[str] | None = None,
    ) -> None:
        cmd = ["docker", "run", "--name", container_name]
        if detach:
            cmd.append("-d")
        for key, val in env.items():
            cmd += ["-e", f"{key}={val}"]
        # lead-wdvx: supplementary groups (e.g. the host docker socket's gid)
        # so the container's non-root default user can use a mounted socket.
        for gid in group_add or []:
            cmd += ["--group-add", str(gid)]
        for mount_type, source, dest, readonly in mounts:
            spec = f"type={mount_type},source={source},target={dest}"
            if readonly:
                spec += ",readonly"
            cmd += ["--mount", spec]
        if network:
            cmd += ["--network", network]
        cmd.append(image)
        cmd += ["sleep", "infinity"]
        self._last_command = cmd
        subprocess.run(cmd, check=True)

    def pull(self, image_ref: str) -> None:
        # Scenario af2f03d3ac519cb5: fetch the registry-current content for
        # ``image_ref`` (a digest-pinned reference resolved from the moving
        # ``latest`` tag) into the local cache before ``run`` starts the
        # container, so the republished image (D_new) reaches the new container
        # instead of the stale cached ``latest`` (D_old).
        cmd = ["docker", "pull", image_ref]
        self._last_command = cmd
        subprocess.run(cmd, check=True)

    def exec_run(
        self,
        container_name: str,
        command: list[str],
        user: str | None = None,
        env: dict[str, str] | None = None,
        detach: bool = False,
    ) -> subprocess.CompletedProcess:
        # lead-lwk4 R7: ``detach=True`` issues ``docker exec -d`` so the docker
        # daemon runs the command in the BACKGROUND and the exec RETURNS
        # IMMEDIATELY without attaching to (or reading) the command's
        # stdout/stderr.  This is how the fabro ENGAGE returns after starting the
        # foreground fabro server: without ``-d`` the synchronous
        # ``subprocess.run`` reads the exec's pipes to EOF and BLOCKS for the
        # server's lifetime (nohup-inside-the-script does not detach the child
        # stdio from the exec pipes).  ``docker exec -d`` prints nothing and
        # returns at once.
        cmd = ["docker", "exec"]
        if detach:
            cmd.append("-d")
        if user is not None:
            cmd += ["-u", user]
        if env:
            for key, val in env.items():
                cmd += ["-e", f"{key}={val}"]
        cmd += [container_name] + command
        self._last_command = cmd
        return subprocess.run(cmd, capture_output=True, text=True, check=False)

    def exec_interactive(
        self,
        container_name: str,
        command: list[str],
        user: str | None = None,
    ) -> None:
        import os
        cmd = ["docker", "exec", "-it"]
        if user is not None:
            cmd += ["-u", user]
        cmd += [container_name] + command
        self._last_command = cmd
        os.execvp("docker", cmd)

    def stop(self, container_name: str) -> None:
        cmd = ["docker", "rm", "-f", container_name]
        self._last_command = cmd
        subprocess.run(cmd, check=True)

    def host_socket_gid(self, socket_path: str) -> int | None:
        """Stat the host docker socket and return its owning gid (lead-wdvx).

        Returns None when the socket path does not exist or cannot be stat-ed,
        so a launch with the docker-socket flag against a host that has no
        socket adds no ``--group-add`` rather than crashing.
        """
        import os as _os
        try:
            return _os.stat(socket_path).st_gid
        except OSError:
            return None

    def list_bc_containers(self) -> list[ContainerInfo]:
        # list all containers (running + stopped) whose name starts with bc-
        result = subprocess.run(
            ["docker", "ps", "-a", "--filter", "name=^bc-",
             "--format", "{{.Names}}\t{{.Status}}"],
            capture_output=True, text=True, check=False,
        )
        # lead-pixf (010e776c): a non-zero docker exit caused by an
        # unreachable daemon socket is an INFRA failure, NOT an empty list.
        # `docker ps` prints "Cannot connect to the Docker daemon at <sock>"
        # to stderr in that case.  Raise so the controller can exit non-zero
        # naming the socket instead of masking the outage as "No BC
        # containers found".
        if result.returncode != 0 and _is_docker_socket_unreachable(
            result.stderr
        ):
            raise DockerSocketUnreachableError(result.stderr.strip())
        infos: list[ContainerInfo] = []
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            parts = line.split("\t", 1)
            name = parts[0].strip()
            status = parts[1].strip() if len(parts) > 1 else ""
            running = status.lower().startswith("up")
            infos.append(ContainerInfo(name=name, running=running))
        return infos

    def get_mounts(self, container_name: str) -> list[ContainerMount]:
        result = subprocess.run(
            ["docker", "inspect", "--format",
             "{{range .Mounts}}{{.Type}}\t{{.Source}}\t{{.Destination}}\n{{end}}",
             container_name],
            capture_output=True, text=True, check=False,
        )
        mounts: list[ContainerMount] = []
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                mounts.append(ContainerMount(
                    type=parts[0].strip(),
                    source=parts[1].strip(),
                    destination=parts[2].strip(),
                ))
        return mounts

    def agent_online(self, container_name: str) -> bool:
        # lead-pixf (f2ddd6c7 / aeebb281).  Agent presence requires ALL of:
        #   1. the vscode-owned "agent" tmux session exists, AND
        #   2. a live `claude` process is running inside the container, AND
        #   3. that agent has armed its `shop-msg watch` inbox watcher
        #      (a live `shop-msg watch` process).
        # tmux client calls against the vscode-owned session must run as
        # vscode (tmux refuses cross-user attach); the process probes run in
        # the container's own pid namespace.
        has_session = subprocess.run(
            ["docker", "exec", "-u", "vscode", container_name,
             "tmux", "has-session", "-t", "agent"],
            capture_output=True, text=True, check=False,
        )
        if has_session.returncode != 0:
            return False
        claude_live = subprocess.run(
            ["docker", "exec", container_name, "pgrep", "-f", "claude"],
            capture_output=True, text=True, check=False,
        )
        if claude_live.returncode != 0:
            return False
        watch_armed = subprocess.run(
            ["docker", "exec", container_name, "pgrep", "-f", "shop-msg watch"],
            capture_output=True, text=True, check=False,
        )
        return watch_armed.returncode == 0

    def last_command(self) -> list[str]:
        return self._last_command

    def network_exists(self, network_name: str) -> bool:
        result = subprocess.run(
            ["docker", "network", "ls", "--filter", f"name=^{network_name}$",
             "--format", "{{.Name}}"],
            capture_output=True, text=True, check=False,
        )
        return network_name in result.stdout.split()

    def network_create(self, network_name: str) -> None:
        cmd = ["docker", "network", "create", network_name]
        self._last_command = cmd
        subprocess.run(cmd, check=True)

    def messaging_db_reachable(
        self, dsn: str, container: str | None = None
    ) -> bool:
        """Probe the messaging database at ``dsn`` for reachability.

        lead-cs7k: when ``container`` is supplied the probe is executed from
        INSIDE the launched container's network context (via ``docker exec``)
        rather than from the launcher host process.  The launcher host is not
        attached to a second product's docker network, so a host-process
        connect to e.g. ``dummyco-postgres:5432`` false-fails even though the
        container reaches it fine; running the probe inside the container
        makes probe reachability match the container's reachability.  When
        ``container`` is None the legacy host-process probe is used.

        Uses ``shop-msg ping`` if available, falling back to a TCP connect
        against the host:port parsed from the DSN.  Any failure (unparseable
        DSN, refused connection, timeout) returns False.
        """
        if not dsn:
            return False
        host, port = _parse_host_port(dsn, default_port=5432)
        if host is None:
            return False
        if container is not None:
            # Run the probe inside the container's network context.  Prefer the
            # messaging BC's own readiness probe, falling back to a python TCP
            # connect — both execute from inside the container so they resolve
            # the product-network service hostnames the launcher host cannot.
            return self._exec_probe_reachable(
                container,
                ping_cmd=["shop-msg", "ping", "--dsn", dsn],
                host=host,
                port=port,
            )
        # Legacy host-process probe (container is None).
        probe = subprocess.run(
            ["shop-msg", "ping", "--dsn", dsn],
            capture_output=True, text=True, check=False,
        )
        if probe.returncode == 0:
            return True
        import socket
        try:
            with socket.create_connection((host, port), timeout=2.0):
                return True
        except (OSError, ValueError):
            return False

    def health_status(self, container_name: str) -> str:
        """Read the container's Docker health status via docker inspect."""
        result = subprocess.run(
            ["docker", "inspect", "--format",
             "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}",
             container_name],
            capture_output=True, text=True, check=False,
        )
        status = result.stdout.strip()
        return status or "none"

    def agent_vault_reachable(
        self, broker_address: str, container: str | None = None
    ) -> bool:
        """Probe the agent-vault broker at ``broker_address`` for reachability.

        lead-cs7k: as for ``messaging_db_reachable``, when ``container`` is
        supplied the probe runs from INSIDE the launched container's network
        context (``docker exec``) so it resolves the product-network broker
        host (e.g. ``dummyco-agent-vault``) the launcher host cannot.

        Parses a host:port (or URL) out of ``broker_address`` and attempts a
        TCP connect.  Any failure (empty/unparseable address, refused
        connection, timeout) returns False so launch fails fast with a
        readiness error rather than engaging an agent that can authenticate
        to nothing.
        """
        if not broker_address:
            return False
        host, port = _parse_host_port(broker_address)
        if host is None or port is None:
            return False
        if container is not None:
            # Run the probe inside the container's network context.
            return self._exec_probe_reachable(
                container, ping_cmd=None, host=host, port=port
            )
        import socket
        try:
            with socket.create_connection((host, port), timeout=2.0):
                return True
        except (OSError, ValueError):
            return False

    def _exec_probe_reachable(
        self,
        container: str,
        *,
        ping_cmd: list[str] | None,
        host: str,
        port: int,
    ) -> bool:
        """Execute a reachability probe FROM INSIDE the container's network.

        The container is attached to the product's docker network, so a probe
        executed via ``docker exec`` resolves the product-network service
        hostnames the launcher host process cannot (the lead-cs7k
        second-product bug).  Prefers ``ping_cmd`` when supplied (e.g.
        ``shop-msg ping``); otherwise falls back to a python TCP connect run
        inside the container.  Any non-zero / error result returns False.
        """
        if ping_cmd is not None:
            result = self.exec_run(container, ping_cmd)
            if result.returncode == 0:
                return True
        # Fall back to a python TCP connect executed inside the container.
        connect_script = (
            "import socket,sys\n"
            f"\ntry:\n"
            f"    socket.create_connection(({host!r}, {port}), timeout=2.0)"
            f"\nexcept Exception:\n    sys.exit(1)\n"
        )
        result = self.exec_run(container, ["python3", "-c", connect_script])
        return result.returncode == 0

    def write_launch_diagnostic(
        self, host_path: str, content: str
    ) -> None:
        """Persist a launch-failure diagnostic to a host file (lead-63em).

        Writes ``content`` to ``host_path`` on the launcher host's own
        filesystem — the SAME host-visible per-BC surface the mailbox is read
        from — creating any missing parent directories.  This is a plain host
        write (NOT a ``docker exec`` into the container): the file must be
        readable from the host even when no container / tmux session ever came
        up, so it cannot live inside the container.  The launch process exits
        after writing, so the file persists independently of the launch
        command's stderr and of the bc-container monitor tmux pane.
        """
        from pathlib import Path as _Path
        p = _Path(host_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def capture_pane(
        self, container_name: str, tmux_session: str
    ) -> str:
        """One-shot ``tmux capture-pane`` of the named session's pane.

        lead-q3uy — the engage path reads the rendered pane once (not a poll)
        to classify a blocking interactive option screen.  Runs as vscode for
        the same reason every other tmux client call does (the agent tmux
        server is vscode-owned and tmux refuses a cross-user connection).
        """
        result = subprocess.run(
            ["docker", "exec", "-u", "vscode", container_name,
             "tmux", "capture-pane", "-p", "-t", tmux_session],
            capture_output=True, text=True, check=False,
        )
        return result.stdout

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
        """Poll tmux capture-pane until ``marker`` is observed.

        lead-j351 — marker-keyed (progress-based) wait.  The wait keys on the
        observable ``marker`` rather than abandoning at a fixed wall-clock
        deadline.  ``timeout_seconds`` is a *no-progress / idle* budget, NOT
        an absolute cap: as long as the pane contents keep CHANGING (the boot
        is still making progress toward readiness) the wait keeps polling,
        even past the legacy 60s deadline.  It only abandons once the pane has
        gone idle (no change) for ``timeout_seconds`` without the marker
        appearing.

        This fixes the live v0.3.3 defect where a *brokered* boot reaches the
        agent REPL but takes >60s: the old fixed 60s deadline fired before the
        input-ready marker appeared and dropped prompt injection.  A slow
        brokered boot that is still progressing now still gets its marker
        observed and its prompt injected.

        ``_clock`` and ``_capture`` are injectable test seams (a monotonic /
        sleep source and a pane-capture callable); production uses the real
        monotonic clock and ``docker exec ... tmux capture-pane``.
        """
        clock = _clock if _clock is not None else time
        # The agent tmux session is owned by vscode (see
        # controller.AGENT_CONTAINER_USER); capture-pane against a
        # vscode-owned tmux server must run as vscode or tmux refuses the
        # cross-user connection.
        capture_cmd = [
            "docker", "exec", "-u", "vscode", container_name,
            "tmux", "capture-pane", "-p", "-t", tmux_session,
        ]

        def _capture_pane() -> str:
            if _capture is not None:
                return _capture()
            result = subprocess.run(
                capture_cmd, capture_output=True, text=True, check=False,
            )
            return result.stdout

        last_pane: str | None = None
        # Wall-clock instant of the last observed PROGRESS (pane change).  The
        # wait abandons only after `timeout_seconds` of NO progress with the
        # marker still absent.
        last_progress_at = clock.monotonic()
        while True:
            pane = _capture_pane()
            if marker in pane:
                return True
            now = clock.monotonic()
            if pane != last_pane:
                # The boot is still progressing toward readiness; reset the
                # idle budget so a slow-but-advancing boot is not abandoned at
                # a fixed wall-clock deadline.
                last_pane = pane
                last_progress_at = now
            elif now - last_progress_at >= timeout_seconds:
                # No progress for the full idle budget AND the marker never
                # appeared — abandon.
                return False
            clock.sleep(poll_interval_seconds)


# ---------------------------------------------------------------------------
# Real registry driver (shells out to docker buildx imagetools)
# ---------------------------------------------------------------------------

class RealRegistryDriver:
    """Production RegistryDriver that resolves a tag's current registry digest.

    Uses ``docker buildx imagetools inspect --format '{{.Manifest.Digest}}'``
    to read the registry-current digest for the given image reference.  Falls
    back to the original reference if resolution fails (so launch is not made
    strictly dependent on registry reachability at run time).
    """

    def resolve_digest(self, image_ref: str) -> str:
        result = subprocess.run(
            ["docker", "buildx", "imagetools", "inspect",
             "--format", "{{.Manifest.Digest}}", image_ref],
            capture_output=True, text=True, check=False,
        )
        digest = result.stdout.strip()
        return digest or image_ref

