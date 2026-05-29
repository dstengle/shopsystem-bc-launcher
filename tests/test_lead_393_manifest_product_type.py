"""
Unit coverage for lead-393: bc-manifest.yaml `product:` field type-check.

Before this fix, a manifest whose `product:` value parsed to a non-string
YAML scalar (e.g. ``product: 42``, ``product: true``, ``product: null``)
caused ``_read_product_from_manifest`` to return that non-string value,
which the launch path then passed to ``_slugify``.  ``_slugify`` invokes
``.strip()`` / ``.lower()`` on its argument, so the user saw an uncaught
``AttributeError: 'int' object has no attribute 'strip'`` traceback that
did not name the malformed field or the manifest file.

This module pins:
  1. ``_read_product_from_manifest`` raises ``ManifestProductTypeError``
     (not ``AttributeError``) for each non-string scalar shape.
  2. The exception carries field name, manifest path, expected type, and
     observed type — the four facts the user needs to fix the manifest.
  3. ``controller.launch`` translates the exception into
     ``CommandResult(exit_code=1, stderr=<clean single-line message>)``
     instead of letting it propagate as a traceback.
  4. ``controller.launch(debug=True)`` opts back into the raw exception
     so operators can still get a traceback when they want one.
  5. The clean-stderr path is reached by the public CLI
     (``bc-container launch ...``) with no traceback on stderr.
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

from bc_launcher.cli import main as cli_main
from bc_launcher.controller import (
    BcContainerController,
    ManifestProductTypeError,
    _read_product_from_manifest,
)
from tests.fake_driver import FakeDockerDriver


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_manifest(tmp_path: Path, product_value) -> Path:
    """Write a bc-manifest.yaml whose `product:` field is ``product_value``.

    Uses an explicit YAML literal for ``product`` so we exercise the
    yaml-parsed scalar path (int, bool, null, list, dict) rather than
    coercing through a string.
    """
    manifest_path = tmp_path / "bc-manifest.yaml"
    data = {
        "product": product_value,
        "bcs": [
            {
                "name": "shopsystem-messaging",
                "remote": "https://github.com/dstengle/shopsystem-messaging.git",
                "role": "bc",
            }
        ],
    }
    manifest_path.write_text(yaml.dump(data, default_flow_style=False))
    return manifest_path


def _make_credential_home(tmp_path: Path) -> Path:
    home = tmp_path / "fake_home"
    home.mkdir(parents=True, exist_ok=True)
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    (home / ".config" / "gh").mkdir(parents=True, exist_ok=True)
    (home / ".gitconfig").write_text("")
    return home


# ---------------------------------------------------------------------------
# _read_product_from_manifest type-check
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "product_value, observed_type",
    [
        (42, "int"),
        (True, "bool"),
        (3.14, "float"),
        (["a", "b"], "list"),
        ({"nested": "dict"}, "dict"),
    ],
)
def test_read_product_raises_for_non_string_scalars(
    tmp_path: Path, product_value, observed_type: str
) -> None:
    manifest_path = _write_manifest(tmp_path, product_value)

    with pytest.raises(ManifestProductTypeError) as excinfo:
        _read_product_from_manifest(manifest_path)

    err = excinfo.value
    assert err.field == "product"
    assert err.manifest_path == manifest_path
    assert err.expected_type == "string"
    assert err.observed_type == observed_type


def test_read_product_raises_for_explicit_null(tmp_path: Path) -> None:
    """`product: null` is a present-but-malformed field, not an absent one.

    It must surface as ManifestProductTypeError naming `null` rather than
    silently falling through to the "no network" branch — otherwise the
    operator sees an unrelated error and cannot find the malformed field.
    """
    manifest_path = tmp_path / "bc-manifest.yaml"
    manifest_path.write_text("product: null\nbcs: []\n")

    with pytest.raises(ManifestProductTypeError) as excinfo:
        _read_product_from_manifest(manifest_path)

    assert excinfo.value.observed_type == "null"


def test_read_product_returns_string_for_valid_string(tmp_path: Path) -> None:
    """Happy path: a plain string product value continues to round-trip."""
    manifest_path = _write_manifest(tmp_path, "shopsystem product")
    assert _read_product_from_manifest(manifest_path) == "shopsystem product"


def test_read_product_returns_none_when_key_absent(tmp_path: Path) -> None:
    """An absent `product:` key is distinct from a malformed one: returns None."""
    manifest_path = tmp_path / "bc-manifest.yaml"
    manifest_path.write_text("bcs: []\n")
    assert _read_product_from_manifest(manifest_path) is None


def test_read_product_returns_none_when_file_missing(tmp_path: Path) -> None:
    """An absent file returns None, same as before."""
    manifest_path = tmp_path / "no-such-manifest.yaml"
    assert _read_product_from_manifest(manifest_path) is None


# ---------------------------------------------------------------------------
# format_message: stderr-ready single-line message naming the four facts
# ---------------------------------------------------------------------------

def test_format_message_names_field_path_expected_observed(tmp_path: Path) -> None:
    manifest_path = tmp_path / "bc-manifest.yaml"
    manifest_path.write_text("product: 42\n")
    err = ManifestProductTypeError(
        manifest_path=manifest_path, observed_type="int"
    )
    msg = err.format_message()
    # All four facts required by lead-393 acceptance criterion 2 must appear:
    assert "product" in msg
    assert str(manifest_path) in msg
    assert "string" in msg  # expected
    assert "int" in msg  # observed
    # Single line, no newlines
    assert "\n" not in msg


# ---------------------------------------------------------------------------
# controller.launch: clean exit + no traceback for malformed product
# ---------------------------------------------------------------------------

def test_launch_with_int_product_exits_nonzero_with_clean_stderr(
    tmp_path: Path,
) -> None:
    driver = FakeDockerDriver()
    controller = BcContainerController(driver)
    manifest_path = _write_manifest(tmp_path, 42)
    credential_home = _make_credential_home(tmp_path)

    result = controller.launch(
        bc_name="shopsystem-messaging",
        manifest_path=manifest_path,
        credential_home=credential_home,
    )

    assert result.exit_code != 0, "malformed product must produce non-zero exit"
    assert "product" in result.stderr
    assert str(manifest_path) in result.stderr
    assert "string" in result.stderr
    assert "int" in result.stderr
    # No container should have been started for a manifest that fails the
    # type-check at the network-derivation step.
    assert not driver.is_running("bc-shopsystem-messaging")


def test_launch_with_null_product_exits_nonzero_with_clean_stderr(
    tmp_path: Path,
) -> None:
    driver = FakeDockerDriver()
    controller = BcContainerController(driver)
    manifest_path = tmp_path / "bc-manifest.yaml"
    manifest_path.write_text("product: null\nbcs: []\n")
    credential_home = _make_credential_home(tmp_path)

    result = controller.launch(
        bc_name="shopsystem-messaging",
        manifest_path=manifest_path,
        credential_home=credential_home,
    )

    assert result.exit_code != 0
    assert "product" in result.stderr
    assert "null" in result.stderr
    assert "string" in result.stderr


def test_launch_with_bool_product_exits_nonzero_with_clean_stderr(
    tmp_path: Path,
) -> None:
    driver = FakeDockerDriver()
    controller = BcContainerController(driver)
    manifest_path = _write_manifest(tmp_path, True)
    credential_home = _make_credential_home(tmp_path)

    result = controller.launch(
        bc_name="shopsystem-messaging",
        manifest_path=manifest_path,
        credential_home=credential_home,
    )

    assert result.exit_code != 0
    assert "product" in result.stderr
    assert "bool" in result.stderr
    assert "string" in result.stderr


def test_launch_debug_mode_propagates_typed_exception(tmp_path: Path) -> None:
    """In debug mode, the operator opts back into the raw traceback path."""
    driver = FakeDockerDriver()
    controller = BcContainerController(driver)
    manifest_path = _write_manifest(tmp_path, 42)
    credential_home = _make_credential_home(tmp_path)

    with pytest.raises(ManifestProductTypeError):
        controller.launch(
            bc_name="shopsystem-messaging",
            manifest_path=manifest_path,
            credential_home=credential_home,
            debug=True,
        )


def test_launch_with_explicit_network_skips_manifest_typecheck(
    tmp_path: Path,
) -> None:
    """An explicit --network flag short-circuits the manifest read entirely.

    The malformed `product:` field must NOT block a launch that doesn't
    derive the network from the manifest in the first place.
    """
    driver = FakeDockerDriver()
    controller = BcContainerController(driver)
    manifest_path = _write_manifest(tmp_path, 42)
    credential_home = _make_credential_home(tmp_path)

    result = controller.launch(
        bc_name="shopsystem-messaging",
        manifest_path=manifest_path,
        credential_home=credential_home,
        network="custom-net",
    )

    assert result.exit_code == 0, (
        f"explicit --network must bypass manifest product type check; "
        f"got exit_code={result.exit_code} stderr={result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# cli.main: exit code + stderr observable via the documented entry point
# ---------------------------------------------------------------------------

def test_cli_main_with_int_product_writes_clean_stderr_no_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """`bc-container launch` with `product: 42` exits non-zero with no traceback.

    Exercises the CLI through ``cli_main`` (the public ``bc-container``
    entry point) rather than the controller directly, so the test pins
    the user-observable behavior named in lead-393 acceptance criterion 2.
    """
    manifest_path = _write_manifest(tmp_path, 42)
    credential_home = _make_credential_home(tmp_path)

    # Point HOME at the credential_home so the launch's credential-discovery
    # branch finds the staged dotfiles and reaches the manifest type-check.
    monkeypatch.setenv("HOME", str(credential_home))
    # Make sure no leftover BCLAUNCHER_DEBUG from the environment opts us
    # into the traceback path.
    monkeypatch.delenv("BCLAUNCHER_DEBUG", raising=False)
    monkeypatch.chdir(tmp_path)

    # Swap the real Docker driver out for the fake so cli_main doesn't try
    # to contact a real daemon when the type-check path is bypassed in
    # other tests; the type-check failure happens before any docker call,
    # so even without this the test would work — but doing it keeps the
    # test self-contained.
    import bc_launcher.cli as cli_module
    monkeypatch.setattr(cli_module, "RealDockerDriver", FakeDockerDriver)

    exit_code = cli_main([
        "launch", "shopsystem-messaging",
        # Provide a no-op startup_prompt so the launch path doesn't try to
        # touch Claude Code readiness; it will never get past the manifest
        # type-check anyway.
        "--startup-prompt", "",
    ])

    captured = capsys.readouterr()
    assert exit_code != 0
    assert "product" in captured.err
    assert "string" in captured.err
    assert "int" in captured.err
    # No traceback frames in non-debug mode.
    assert "Traceback" not in captured.err
    assert "AttributeError" not in captured.err


def test_cli_main_debug_flag_propagates_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`bc-container launch --debug` lets the typed exception propagate."""
    manifest_path = _write_manifest(tmp_path, 42)
    credential_home = _make_credential_home(tmp_path)

    monkeypatch.setenv("HOME", str(credential_home))
    monkeypatch.delenv("BCLAUNCHER_DEBUG", raising=False)
    monkeypatch.chdir(tmp_path)

    import bc_launcher.cli as cli_module
    monkeypatch.setattr(cli_module, "RealDockerDriver", FakeDockerDriver)

    with pytest.raises(ManifestProductTypeError):
        cli_main([
            "launch", "shopsystem-messaging",
            "--debug",
            "--startup-prompt", "",
        ])


def test_cli_main_bclauncher_debug_env_var_propagates_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`BCLAUNCHER_DEBUG=1 bc-container launch` is equivalent to --debug."""
    manifest_path = _write_manifest(tmp_path, 42)
    credential_home = _make_credential_home(tmp_path)

    monkeypatch.setenv("HOME", str(credential_home))
    monkeypatch.setenv("BCLAUNCHER_DEBUG", "1")
    monkeypatch.chdir(tmp_path)

    import bc_launcher.cli as cli_module
    monkeypatch.setattr(cli_module, "RealDockerDriver", FakeDockerDriver)

    with pytest.raises(ManifestProductTypeError):
        cli_main([
            "launch", "shopsystem-messaging",
            "--startup-prompt", "",
        ])
