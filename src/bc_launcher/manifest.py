"""
Manifest loading, validation, and sync logic for bc-container manifest commands.

The manifest is a YAML file (bc-manifest.yaml) at the lead repo root. Its structure:

  product: shopsystem            # optional top-level product slug (string)
  bcs:
    - name: shopsystem-messaging
      remote: https://github.com/dstengle/shopsystem-messaging.git
      role: bc
    - ...

Required fields per entry: name, remote, role.

Optional top-level field: ``product`` (string).  When present it is the
shared MIDDLE tier of the unified product-slug resolver (lead-53y0): the
docker network name, the BC-name-shape prefix, and the injected
SHOPMSG_SYSTEM_SLUG all derive from it, with each surface's own env/flag
override layered on top.  Absent or 'shopsystem' keeps the default
'shopsystem' identity on every surface unchanged.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import yaml


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

REQUIRED_FIELDS = ("name", "remote", "role")

GITHUB_URL_RE = re.compile(
    r"^(https://github\.com/[\w.\-]+/[\w.\-]+(\.git)?|git@github\.com:[\w.\-]+/[\w.\-]+(\.git)?)$"
)

# Product-slug resolution.
#
# Historically the accepted BC-name shape was hard-coded to the
# 'shopsystem-' prefix, which product-locked the validator: a non-shopsystem
# product (e.g. 'acme') could not declare BC names like 'acme-widget'.  The
# accepted prefix is now derived from the configured product slug.
#
# Precedence mirrors the SHOPMSG_DSN / BC_IMAGE idiom in controller.py
# (flag -> env -> default): an explicit ``product_slug`` argument wins;
# otherwise the PRODUCT_SLUG process-env var; otherwise the built-in
# default 'shopsystem'.  With the default slug the accepted set is
# unchanged — 'shopsystem-*' names still validate.
PRODUCT_SLUG_ENV = "PRODUCT_SLUG"
DEFAULT_PRODUCT_SLUG = "shopsystem"


def resolve_product_slug(
    product_slug: str | None = None,
    manifest_product: str | None = None,
) -> str:
    """Resolve the configured product slug (flag -> env -> manifest -> default).

    This is the UNIFIED product-slug resolver (lead-53y0).  The manifest
    ``product:`` field is the shared MIDDLE tier across all three identity
    surfaces (injected system slug, BC-name-shape prefix, docker network name);
    each surface layers its own per-surface env/flag override on top.

    Precedence for THIS surface (name-shape): an explicit ``product_slug``
    argument (the --product-slug flag) wins; otherwise the PRODUCT_SLUG
    process-env var; otherwise the manifest ``product:`` value passed as
    ``manifest_product``; otherwise DEFAULT_PRODUCT_SLUG ('shopsystem').

    The manifest tier is wired in WITHOUT changing the default-slug behavior:
    when neither flag nor env is set AND the manifest declares no product (or
    declares 'shopsystem'), the resolved slug is 'shopsystem' exactly as
    before — preserving the lead-xntx default-slug guarantee.
    """
    if product_slug:
        return product_slug
    env_slug = os.environ.get(PRODUCT_SLUG_ENV)
    if env_slug:
        return env_slug
    if manifest_product:
        return manifest_product
    return DEFAULT_PRODUCT_SLUG


def bc_name_re_for_slug(slug: str) -> re.Pattern[str]:
    """Build the canonical BC-name pattern for a given product slug.

    The accepted shape is '<slug>-<identifier>' where <identifier> begins
    with a lowercase letter followed by lowercase alphanumerics and hyphens.
    """
    return re.compile(rf"^{re.escape(slug)}-[a-z][a-z0-9\-]+$")


# Default module-level pattern for the default product slug ('shopsystem').
# Retained for backward compatibility with consumers that import BC_NAME_RE
# directly (e.g. manifest-shape BDD assertions over shopsystem-* names).
BC_NAME_RE = bc_name_re_for_slug(DEFAULT_PRODUCT_SLUG)


@dataclass
class BcEntry:
    name: str
    remote: str
    role: str


@dataclass
class Manifest:
    entries: list[BcEntry] = field(default_factory=list)
    # Top-level product slug (lead-53y0).  None when the manifest declares no
    # ``product:`` field.  Shared middle tier of the unified product resolver.
    product: str | None = None


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_manifest(path: Path) -> Manifest:
    """Parse bc-manifest.yaml and return a Manifest object.

    Raises yaml.YAMLError on parse failure.
    Raises ValueError if the top-level structure is not a dict with a 'bcs' key.
    """
    text = path.read_text()
    data = yaml.safe_load(text)
    if not isinstance(data, dict) or "bcs" not in data:
        raise ValueError("Manifest must be a YAML mapping with a 'bcs' key")
    entries: list[BcEntry] = []
    for item in (data.get("bcs") or []):
        entry = BcEntry(
            name=item.get("name", ""),
            remote=item.get("remote", ""),
            role=item.get("role", ""),
        )
        entries.append(entry)
    # Top-level ``product:`` (lead-53y0) is an optional string.  A non-string
    # value (int/bool/list/dict) is normalized to None here; the launch path's
    # _read_product_from_manifest enforces the string type with a clean error
    # (lead-393) for the network/system-slug derivation.
    raw_product = data.get("product")
    product = raw_product if isinstance(raw_product, str) and raw_product else None
    return Manifest(entries=entries, product=product)


# ---------------------------------------------------------------------------
# Validation result
# ---------------------------------------------------------------------------

@dataclass
class ValidationResult:
    ok: bool
    messages: list[str] = field(default_factory=list)
    validated: list[str] = field(default_factory=list)   # names of BCs that passed
    failed: list[str] = field(default_factory=list)      # names of BCs that failed


# ---------------------------------------------------------------------------
# GitHub driver protocol
# ---------------------------------------------------------------------------

class GitHubDriver(Protocol):
    """Check whether a GitHub remote URL is reachable."""

    def is_reachable(self, url: str) -> bool:
        """Return True if the remote URL is reachable (repository exists)."""
        ...


class RealGitHubDriver:
    """Production GitHubDriver using git ls-remote."""

    def is_reachable(self, url: str) -> bool:
        import subprocess
        result = subprocess.run(
            ["git", "ls-remote", "--exit-code", url],
            capture_output=True, timeout=15,
        )
        return result.returncode == 0


# ---------------------------------------------------------------------------
# Git driver protocol (for sync clones)
# ---------------------------------------------------------------------------

class GitDriver(Protocol):
    """Minimal git operations for sync."""

    def clone(self, remote_url: str, dest: Path) -> None:
        """Clone remote_url into dest directory."""
        ...

    def get_remote_url(self, repo_path: Path) -> str | None:
        """Return the 'origin' remote URL of the repo at repo_path, or None."""
        ...


class RealGitDriver:
    """Production GitDriver using subprocess git."""

    def clone(self, remote_url: str, dest: Path) -> None:
        import subprocess
        subprocess.run(
            ["git", "clone", remote_url, str(dest)],
            check=True,
        )

    def get_remote_url(self, repo_path: Path) -> str | None:
        import subprocess
        result = subprocess.run(
            ["git", "-C", str(repo_path), "remote", "get-url", "origin"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return None

# ManifestController split out (re-exported for compat):
from bc_launcher.manifest_controller import (  # noqa: F401,E402
    ManifestController,
)


# ---------------------------------------------------------------------------
# Launch-time manifest reads (moved from controller, Phase 1 decomposition).
# ---------------------------------------------------------------------------


class ManifestProductTypeError(Exception):
    """Raised when a bc-manifest.yaml file's `product:` field is not a string.

    Carries enough structured context that the CLI can format a single-line
    error message naming the field, file path, expected type, and observed
    type — without exposing the underlying ``AttributeError`` that would
    otherwise surface from ``_slugify`` downstream.

    Per lead-393: the launch path must convert this into a non-zero exit
    with a clean stderr message; a Python traceback is only acceptable when
    the operator opts in via ``--debug`` (or ``BCLAUNCHER_DEBUG=1``).
    """

    def __init__(
        self,
        manifest_path: Path,
        observed_type: str,
        *,
        field: str = "product",
        expected_type: str = "string",
    ) -> None:
        self.manifest_path = manifest_path
        self.field = field
        self.expected_type = expected_type
        self.observed_type = observed_type
        super().__init__(self.format_message())

    def format_message(self) -> str:
        """Single-line stderr-ready message naming field, file, types."""
        return (
            f"bc-manifest.yaml: field {self.field!r} in {self.manifest_path} "
            f"has wrong type: expected {self.expected_type}, "
            f"got {self.observed_type}"
        )


def _resolve_manifest_remote(manifest_path: Path, bc_name: str) -> str | None:
    """Resolve the git remote URL registered for ``bc_name`` in bc-manifest.yaml.

    lead-uiwu FACET 1.  bc-manifest.yaml registers each BC with its remote URL
    and is "the declared source of remote URLs when launching BCs".  When a
    ``bc-container launch`` carries NO ``--repo-url`` and NO
    ``--workspace-mount``, the launcher resolves the BC's clone source from the
    manifest's ``bcs[].remote`` entry for the named BC and clones it into
    ``/workspace`` — rather than starting a container with a SILENT empty,
    non-git ``/workspace``.

    This is DISTINCT from ``_read_product_from_manifest`` (the manifest
    ``product:`` field, used for network/system-slug derivation): this reads the
    per-BC ``remote:`` field.  Returns the remote URL string for ``bc_name``, or
    ``None`` when the manifest is absent / unparseable / carries no resolvable
    remote for that BC, so the caller can apply the no-source loud-failure path
    (FACET 1 negative, scenario 0b50d090c9cc3c45).
    """
    import yaml
    from bc_launcher.manifest import load_manifest
    if not manifest_path.exists():
        return None
    try:
        manifest = load_manifest(manifest_path)
    except (yaml.YAMLError, ValueError):
        return None
    for entry in manifest.entries:
        if entry.name == bc_name:
            remote = (entry.remote or "").strip()
            return remote or None
    return None


def _read_product_from_manifest(manifest_path: Path) -> str | None:
    """Read the 'product' field from a bc-manifest.yaml file.

    Returns None if the file does not exist or has no 'product' key.
    Raises yaml.YAMLError on parse failure.
    Raises ManifestProductTypeError if 'product' is present but not a string.
    """
    import yaml
    if not manifest_path.exists():
        return None
    data = yaml.safe_load(manifest_path.read_text())
    if not isinstance(data, dict):
        return None
    if "product" not in data:
        return None
    product = data["product"]
    if product is None:
        # `product: null` (explicitly null) is just as broken as `product: 42`
        # for the downstream slugify call — name the field rather than silently
        # falling through to the "no network" branch, which would otherwise
        # hide the malformed-field root cause behind an unrelated error.
        raise ManifestProductTypeError(
            manifest_path=manifest_path,
            observed_type="null",
        )
    if not isinstance(product, str):
        raise ManifestProductTypeError(
            manifest_path=manifest_path,
            observed_type=type(product).__name__,
        )
    return product
