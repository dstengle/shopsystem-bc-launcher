r"""Structural regression test for lead-klxi: the bc-base image must bake the
bc-launcher version being RELEASED, not a stale self-pin.

ROOT BUG: docker/bc-base/Dockerfile installs shopsystem-bc-launcher from a
LITERAL VCS self-pin (`shopsystem-bc-launcher ... @vMAJOR.MINOR.PATCH`) that
was left at v0.3.59 while the package itself released v0.3.60..v0.3.65. Only
SHOP_TEMPLATES_VERSION was bumped across those releases; the launcher self-pin
never was. Result: every bc-base / bc-lead image built for those releases baked
the 0.3.59 launcher code — missing the dispatcher.toml BC_NAME rewrite, the
GH_TOKEN-in-runtime-env fix, the N4 pour-wiring read, and the stdin def
placement — even though the ENV/OCI-label surface (lead-5xnd) reported the
release version. The install line is the ground truth for what code is baked;
the ENV surface is cosmetic.

INVARIANT (this test): the launcher self-pin the Dockerfile INSTALLS from, and
the SHOPSYSTEM_BC_LAUNCHER_VERSION ARG default that surfaces it, must both equal
this package's OWN release version (pyproject.toml [project].version). A build
of the vX.Y.Z release tag then installs the launcher code AT that tag — i.e. the
code being released — so the image can never again bake a stale launcher.

Why a LITERAL self-pin (not `@${SHOPSYSTEM_BC_LAUNCHER_VERSION}`): the launcher
self-pin is a POLLED dependency. The centralized poll
(.github/workflows/poll-bc-base-deps.yml, lead-dqje/lead-czwo) resolves the
launcher's own latest release and sed-bumps THIS literal install-line self-pin
(it greps `shopsystem-bc-launcher(?:\.git)?@\Kv[0-9.]+` and rewrites it). The
literal shape is load-bearing for that mechanism and for the framework-CLI pin
scenarios (bc_base_framework_cli_pins, bc_base_self_pin_poll). This test asserts
the literal is CURRENT, not that it is parameterized.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

from tests.conftest import _REPO_ROOT
from tests.support.common import _find_bc_base_dockerfile


def _package_release_version() -> str:
    """The `vMAJOR.MINOR.PATCH` this package releases, from pyproject.toml."""
    pyproject = Path(_REPO_ROOT) / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text())
    version = data["project"]["version"]
    return f"v{version}"


def _launcher_self_pin_version(dockerfile_text: str) -> str | None:
    """The vX.Y.Z the bc-base Dockerfile INSTALLS shopsystem-bc-launcher from."""
    m = re.search(
        r"shopsystem-bc-launcher @ git\+https://github\.com/dstengle/"
        r"shopsystem-bc-launcher(?:\.git)?@(v\d+\.\d+\.\d+)",
        dockerfile_text,
    )
    return m.group(1) if m else None


def _launcher_arg_default(dockerfile_text: str) -> str | None:
    """The SHOPSYSTEM_BC_LAUNCHER_VERSION ARG default (the ENV/label surface)."""
    m = re.search(
        r"ARG\s+SHOPSYSTEM_BC_LAUNCHER_VERSION=(v\d+\.\d+\.\d+)",
        dockerfile_text,
    )
    return m.group(1) if m else None


def test_bc_base_launcher_self_pin_equals_package_release_version():
    """The INSTALLED launcher self-pin equals this package's release version,
    so the released image bakes the released launcher code (not a stale pin)."""
    dockerfile = _find_bc_base_dockerfile()
    assert dockerfile is not None, (
        "No tracked docker/bc-base/Dockerfile found under the bc-launcher repo."
    )
    text = dockerfile.read_text()
    release = _package_release_version()
    installed = _launcher_self_pin_version(text)

    assert installed is not None, (
        "bc-base Dockerfile does not install shopsystem-bc-launcher from a "
        "github.com/dstengle/shopsystem-bc-launcher @ vMAJOR.MINOR.PATCH "
        f"literal self-pin.\nDockerfile content:\n{text}"
    )
    assert installed == release, (
        "bc-base Dockerfile installs a STALE shopsystem-bc-launcher self-pin: "
        f"the install line pins {installed} but this package releases {release}. "
        "The image would bake the launcher code at the stale pin, not the code "
        "being released. Bump the self-pin (and the "
        "SHOPSYSTEM_BC_LAUNCHER_VERSION ARG default) in lockstep with the "
        "release."
    )


def test_bc_base_launcher_arg_default_equals_package_release_version():
    """The SHOPSYSTEM_BC_LAUNCHER_VERSION ARG default (the ENV/OCI-label
    surface) equals the release version, so a plain (non-CI) build surfaces the
    correct baked version and the surface never diverges from the install."""
    dockerfile = _find_bc_base_dockerfile()
    assert dockerfile is not None
    text = dockerfile.read_text()
    release = _package_release_version()
    arg_default = _launcher_arg_default(text)

    assert arg_default is not None, (
        "bc-base Dockerfile does not declare "
        "ARG SHOPSYSTEM_BC_LAUNCHER_VERSION=vMAJOR.MINOR.PATCH."
    )
    assert arg_default == release, (
        "bc-base Dockerfile's SHOPSYSTEM_BC_LAUNCHER_VERSION ARG default "
        f"({arg_default}) does not equal this package's release version "
        f"({release}); a plain build would surface a stale baked-version label."
    )
