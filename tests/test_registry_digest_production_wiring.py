"""Production-wiring tests for the launch digest-resolution path (lead-l5a2h).

These tests exist because the pytest-bdd binding for
@scenario_hash:af2f03d3ac519cb5 passed while production was broken: it injects
a FakeRegistryDriver, so it exercised the controller seam and never the real
launch path's construction site.  bd shopsystem_bc_launcher-7pmt.

Everything here targets the REAL wiring:

* ``build_controller()`` is the real launch path's construction site, and the
  controller it returns must carry a real registry driver (7pmt.1).
* ``RealRegistryDriver.resolve_digest`` must read the MANIFEST digest -- the
  one a ``repo@sha256:...`` pull resolves -- not the config digest, and not via
  the buildx plugin bc-base does not ship (7pmt.2).
* Resolution failure must be loud, not a silent fallback to the bare tag
  (7pmt.3).
"""
from __future__ import annotations

import json
import subprocess

import pytest

from bc_launcher import cli
from bc_launcher.driver import RealDockerDriver, RealRegistryDriver

# Captured verbatim from the real Docker daemon on bc-base against
# ghcr.io/dstengle/shopsystem-bc-base:latest (2026-07-16), trimmed to the keys
# under test.  Note the two DIFFERENT digests: `Descriptor.digest` is the
# MANIFEST digest -- the one `docker pull repo@sha256:...` resolves and the one
# that appears in RepoDigests -- while `SchemaV2Manifest.config.digest` is the
# CONFIG blob digest (which is also the local image Id).  Pulling the config
# digest fails with "manifest unknown"; only the manifest digest is pullable.
MANIFEST_DIGEST = "sha256:b7bed5b6967ae725fbeb4b64baa6a3d56f78f594fae0fdd62c2b92537c8b9c6a"
CONFIG_DIGEST = "sha256:072e68971f7803791e2733a029ae26f540f39ea00472f076f7b0eada7bb7bee8"

REAL_VERBOSE_OUTPUT = json.dumps(
    {
        "Ref": "ghcr.io/dstengle/shopsystem-bc-base:latest",
        "Descriptor": {
            "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
            "digest": MANIFEST_DIGEST,
            "size": 2406,
            "platform": {"architecture": "amd64", "os": "linux"},
        },
        "SchemaV2Manifest": {
            "schemaVersion": 2,
            "config": {"mediaType": "application/vnd.docker.container.image.v1+json",
                       "size": 32263, "digest": CONFIG_DIGEST},
            "layers": [],
        },
    }
)


def _runner_returning(stdout: str, returncode: int = 0, stderr: str = ""):
    """Build a subprocess-runner double that records the argv it was handed."""
    calls: list[list[str]] = []

    def _run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)

    return _run, calls


def test_real_launch_path_controller_carries_a_real_registry_driver():
    """The real launch path must construct a controller with BOTH real drivers.

    Fault 1: cli.py built ``BcContainerController(RealDockerDriver())`` with no
    registry_driver, so controller/_launch_prep.py's digest-resolution + pull
    block was unconditionally skipped on every real launch.
    """
    controller = cli.build_controller()

    assert isinstance(controller._driver, RealDockerDriver)
    assert isinstance(controller._registry_driver, RealRegistryDriver), (
        "the real launch path must inject a RealRegistryDriver; without it "
        "_launch_prep.py skips digest resolution entirely and launch runs "
        "whatever stale digest the local cache holds under 'latest'"
    )


def test_main_launch_obtains_its_controller_from_the_wired_construction_site(
    monkeypatch,
):
    """main()'s launch path must go through build_controller(), not build its own.

    Guards the exact regression shape of fault 1: a correctly-wired factory
    that main() bypasses would re-break production while this module's other
    tests stayed green.
    """

    class _FactoryReached(Exception):
        pass

    def _spy():
        raise _FactoryReached

    monkeypatch.setattr(cli, "build_controller", _spy)

    with pytest.raises(_FactoryReached):
        cli.main(
            ["launch", "shopsystem-messaging", "--repo-url", "https://example.invalid/x.git"]
        )


def test_resolve_digest_does_not_shell_out_to_the_buildx_plugin():
    """Fault 2: bc-base's baked docker-cli ships no buildx plugin.

    Observed live in-container: `docker buildx version` ->
    "docker: 'buildx' is not a docker command."  Resolution must not depend on
    a plugin the runtime image does not have.
    """
    runner, calls = _runner_returning(REAL_VERBOSE_OUTPUT)
    driver = RealRegistryDriver(runner=runner)

    driver.resolve_digest("ghcr.io/dstengle/shopsystem-bc-base:latest")

    assert calls, "resolve_digest must shell out to docker"
    assert "buildx" not in calls[0], f"buildx is unavailable on bc-base: {calls[0]}"
    assert calls[0][:3] == ["docker", "manifest", "inspect"]


def test_resolve_digest_returns_the_pullable_manifest_digest_not_the_config_digest():
    """Resolution must yield the digest a `repo@sha256:...` pull can resolve.

    `docker manifest inspect <ref>` (non-verbose) surfaces `.config.digest`,
    the config blob digest.  Pinning to it produces a reference the daemon
    rejects with "manifest unknown" -- verified live against the real daemon.
    Only `--verbose`'s `.Descriptor.digest` is the pullable manifest digest.
    """
    runner, calls = _runner_returning(REAL_VERBOSE_OUTPUT)
    driver = RealRegistryDriver(runner=runner)

    digest = driver.resolve_digest("ghcr.io/dstengle/shopsystem-bc-base:latest")

    assert digest == MANIFEST_DIGEST
    assert digest != CONFIG_DIGEST, (
        "the config digest is NOT pullable as repo@digest; pinning to it makes "
        "launch fail with 'manifest unknown'"
    )
    assert "--verbose" in calls[0], (
        "the manifest digest is only exposed under --verbose (.Descriptor.digest)"
    )


def test_resolve_digest_fails_loudly_when_resolution_cannot_produce_a_digest():
    """Resolution failure must raise, not silently degrade to the bare tag.

    The old `return digest or image_ref` fallback is precisely what let fault 2
    hide: buildx was absent, the subprocess exited non-zero with empty stdout,
    and launch carried on from the unpinned tag reporting success.  Blast
    radius of raising is bounded: _launch_prep.py pulls the resolved digest
    immediately afterwards, so the launch path already requires the registry to
    be reachable at this point.
    """
    from bc_launcher.driver import DigestResolutionError

    runner, _calls = _runner_returning(
        "", returncode=1, stderr="docker: 'buildx' is not a docker command."
    )
    driver = RealRegistryDriver(runner=runner)

    with pytest.raises(DigestResolutionError) as excinfo:
        driver.resolve_digest("ghcr.io/dstengle/shopsystem-bc-base:latest")

    message = str(excinfo.value)
    assert "ghcr.io/dstengle/shopsystem-bc-base:latest" in message
    assert "is not a docker command" in message, (
        "the underlying docker stderr must reach the operator, not be swallowed"
    )


def test_resolve_digest_fails_loudly_when_the_payload_carries_no_descriptor():
    """A well-formed-but-digestless payload must also raise, not fall back."""
    from bc_launcher.driver import DigestResolutionError

    runner, _calls = _runner_returning(json.dumps({"Ref": "x", "SchemaV2Manifest": {}}))
    driver = RealRegistryDriver(runner=runner)

    with pytest.raises(DigestResolutionError):
        driver.resolve_digest("ghcr.io/dstengle/shopsystem-bc-base:latest")
