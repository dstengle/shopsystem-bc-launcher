"""
Unit tests pinning lead-9r4: assert_docker_run_includes_flag is bound to
the named container.

Background
----------
Before lead-9r4, the step definition

    @then('the FakeDockerDriver records that the docker run command for
           "{container_name}" includes the flag "{flag}"')
    def assert_docker_run_includes_flag(...):
        run_cmd = fake_driver.run_command_for_container(container_name)
        if not run_cmd:
            run_cmd = fake_driver.last_run_command()
        assert run_cmd, "FakeDockerDriver recorded no docker run command"
        ...

silently substituted the *global* last docker-run command for the
named container's run_cmd when no run had been recorded for that name.
In multi-container scenarios this allowed an assertion against container
A to pass whenever container B was launched last with the flag — even
when container A had no recorded run_cmd at all.

The fix removes the ``last_run_command()`` fallback so the step now
binds to ``run_command_for_container(container_name)`` directly and
fails fast (matching the bind-mount / credential-mount peer steps at
conftest.py:1716+) with a message that names the container.

These tests pin:

  (1) When container B is launched with the flag but container A has
      no recorded run, asserting on A fails with the new
      container-naming message ("...no docker run command for 'A'").
  (2) When the named container is launched with the flag the step
      passes (positive control).
  (3) When the named container is launched without the flag the step
      fails with the flag-not-found message (which already names the
      container; unchanged by the fix).
"""
from __future__ import annotations

import pytest

from tests.conftest import assert_docker_run_includes_flag
from tests.fake_driver import FakeDockerDriver


def _make_ctx() -> dict:
    """Step defs accept a ctx dict but this step does not read from it."""
    return {}


def test_lead_9r4_named_container_with_no_recorded_run_fails_even_when_other_container_has_flag():
    """
    Regression: pre-fix, this assertion would pass because the fallback
    substituted container B's run command (which contains the flag) for
    container A's missing run. After the fix it must FAIL with the
    container-naming message.
    """
    fake_driver = FakeDockerDriver()

    # Record a docker run for container B containing flag --network shopsystem-product.
    fake_driver.run(
        container_name="bc-container-b",
        image="some-image:latest",
        env={},
        mounts=[],
        network="shopsystem-product",
        detach=True,
    )

    # No run is recorded for container A.
    assert fake_driver.run_command_for_container("bc-container-a") == []
    # Sanity-check that B's run does contain the flag (so the pre-fix
    # fallback would have produced a false positive).
    assert "--network shopsystem-product" in " ".join(
        fake_driver.run_command_for_container("bc-container-b")
    )

    with pytest.raises(AssertionError) as excinfo:
        assert_docker_run_includes_flag(
            container_name="bc-container-a",
            flag="--network shopsystem-product",
            ctx=_make_ctx(),
            fake_driver=fake_driver,
        )

    # The new failure mode names the container in the message — matching
    # the peer bind-mount / credential-mount step shape.
    assert "FakeDockerDriver recorded no docker run command for 'bc-container-a'" in str(excinfo.value)


def test_lead_9r4_named_container_with_flag_passes():
    """Positive control: when the named container itself was launched with the flag, the step passes."""
    fake_driver = FakeDockerDriver()
    fake_driver.run(
        container_name="bc-container-a",
        image="some-image:latest",
        env={},
        mounts=[],
        network="shopsystem-product",
        detach=True,
    )

    # Must not raise.
    assert_docker_run_includes_flag(
        container_name="bc-container-a",
        flag="--network shopsystem-product",
        ctx=_make_ctx(),
        fake_driver=fake_driver,
    )


def test_lead_9r4_named_container_launched_without_flag_fails_with_flag_message():
    """
    Negative control: when the named container WAS launched but without
    the expected flag, the existing flag-not-found assertion fires (this
    path is unchanged by lead-9r4 and the message already names the container).
    """
    fake_driver = FakeDockerDriver()
    fake_driver.run(
        container_name="bc-container-a",
        image="some-image:latest",
        env={},
        mounts=[],
        network=None,
        detach=True,
    )

    with pytest.raises(AssertionError) as excinfo:
        assert_docker_run_includes_flag(
            container_name="bc-container-a",
            flag="--network shopsystem-product",
            ctx=_make_ctx(),
            fake_driver=fake_driver,
        )

    msg = str(excinfo.value)
    assert "Expected flag '--network shopsystem-product'" in msg
    assert "'bc-container-a'" in msg
