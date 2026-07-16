"""Docker driver package (interface + real implementation).

The DockerDriver protocol is the seam between bc_launcher business logic and the
real Docker daemon. Split into _types/interfaces/real; every public name is
re-exported here so ``from bc_launcher.driver import <name>`` keeps resolving.
"""
from __future__ import annotations

from bc_launcher.driver._types import (  # noqa: F401
    ContainerInfo,
    ContainerMount,
    DigestResolutionError,
    DockerSocketUnreachableError,
)
from bc_launcher.driver.interfaces import DockerDriver, RegistryDriver  # noqa: F401
from bc_launcher.driver.real import RealDockerDriver, RealRegistryDriver  # noqa: F401
from bc_launcher.driver._util import (  # noqa: F401
    _is_docker_socket_unreachable,
    _parse_host_port,
)
