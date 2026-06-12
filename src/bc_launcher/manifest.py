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


# ---------------------------------------------------------------------------
# ManifestController
# ---------------------------------------------------------------------------

class ManifestController:
    """
    Implements bc-container manifest validate / list / sync.

    Accepts GitHubDriver and GitDriver at construction for testability.
    """

    def __init__(
        self,
        github_driver: GitHubDriver | None = None,
        git_driver: GitDriver | None = None,
    ) -> None:
        self._github = github_driver or RealGitHubDriver()
        self._git = git_driver or RealGitDriver()

    # ------------------------------------------------------------------
    # validate
    # ------------------------------------------------------------------

    def validate(
        self,
        manifest_path: Path,
        repos_dir: Path | None = None,
        product_slug: str | None = None,
    ) -> ValidationResult:
        """
        Validate the manifest at manifest_path.

        Checks:
        - YAML parseable
        - required fields present on each entry
        - canonical name pattern (parameterized by the configured product slug)
        - GitHub remote URL format and reachability
        - repos_dir consistency (missing clones, extra dirs, remote mismatches)

        The accepted canonical-name shape is '<product-slug>-<identifier>'.
        ``product_slug`` follows the flag -> env (PRODUCT_SLUG) -> default
        ('shopsystem') precedence; with the default the accepted set is
        unchanged ('shopsystem-*' names still validate).
        """
        result = ValidationResult(ok=True)

        # 1. Parse YAML (FIRST, so the manifest ``product:`` field can feed the
        #    unified product-slug resolver as the shared middle tier — lead-53y0).
        try:
            manifest = load_manifest(manifest_path)
        except yaml.YAMLError as exc:
            result.ok = False
            result.messages.append(f"YAML parse error: {exc}")
            return result
        except ValueError as exc:
            result.ok = False
            result.messages.append(str(exc))
            return result

        result.messages.append("Manifest is syntactically valid")

        # Unified resolver (lead-53y0): name-shape slug is
        #   --product-slug flag > PRODUCT_SLUG env > manifest product: > default.
        # The manifest ``product:`` is the shared middle tier wired in here.
        slug = resolve_product_slug(product_slug, manifest_product=manifest.product)
        # Name-shape enforcement is ADDITIVE: it applies ONLY when the RESOLVED
        # slug is a non-default product slug (via --product-slug flag, PRODUCT_SLUG
        # env, OR a non-'shopsystem' manifest product:).  Under the default slug
        # ('shopsystem' — no flag, no env, and manifest product absent or
        # 'shopsystem') validate() does NOT enforce any name shape, preserving
        # the exact pre-slug-parameterization behavior: 'acme-widget' under the
        # default still validates (lead-xntx default-slug guarantee,
        # cd7571286d97d76d).  The manifest tier MUST NOT re-enable default-slug
        # enforcement — and it does not, because a manifest with no product (or
        # product: shopsystem) resolves to DEFAULT_PRODUCT_SLUG.
        enforce_name_shape = slug != DEFAULT_PRODUCT_SLUG
        name_re = bc_name_re_for_slug(slug)

        # 2. Per-entry checks
        entry_ok = True
        for entry in manifest.entries:
            entry_failed = False

            # Required fields
            for field_name in REQUIRED_FIELDS:
                if not getattr(entry, field_name):
                    result.ok = False
                    entry_failed = True
                    result.messages.append(
                        f"Entry '{entry.name}': missing required field '{field_name}'"
                    )

            # Canonical name pattern, parameterized by product slug.
            # Enforced only when a non-default slug is explicitly configured
            # (additive; default-slug accepted set is unchanged).
            if enforce_name_shape and entry.name and not name_re.match(entry.name):
                result.ok = False
                entry_failed = True
                result.messages.append(
                    f"Entry '{entry.name}': name does not match canonical pattern "
                    f"'{slug}-<identifier>' for product slug '{slug}'"
                )

            # GitHub URL format
            if entry.remote and not GITHUB_URL_RE.match(entry.remote):
                result.ok = False
                entry_failed = True
                result.messages.append(
                    f"Entry '{entry.name}': remote URL '{entry.remote}' is not a valid GitHub URL"
                )

            # GitHub reachability (only if URL format is valid)
            if entry.remote and GITHUB_URL_RE.match(entry.remote):
                if not self._github.is_reachable(entry.remote):
                    result.ok = False
                    entry_failed = True
                    result.messages.append(
                        f"Entry '{entry.name}': remote URL '{entry.remote}' is unreachable "
                        f"(repository not found or connection refused)"
                    )

            if not entry_failed:
                result.validated.append(entry.name)
                result.messages.append(f"Entry '{entry.name}': valid")
            else:
                result.failed.append(entry.name)

        # 3. repos_dir consistency checks
        if repos_dir is not None and repos_dir.exists():
            declared_names = {e.name for e in manifest.entries}
            actual_dirs = {d.name for d in repos_dir.iterdir() if d.is_dir()}

            # Extra dirs not in manifest
            extra = actual_dirs - declared_names
            for name in sorted(extra):
                result.ok = False
                result.messages.append(
                    f"Repos directory contains unexpected entry '{name}' not in manifest"
                )

            # Missing clones
            for entry in manifest.entries:
                clone_path = repos_dir / entry.name
                if not clone_path.exists():
                    result.ok = False
                    result.messages.append(
                        f"Entry '{entry.name}': declared in manifest but no local clone found"
                    )
                elif clone_path.is_dir():
                    # Check remote mismatch
                    actual_url = self._git.get_remote_url(clone_path)
                    if actual_url is not None and actual_url != entry.remote:
                        result.ok = False
                        result.messages.append(
                            f"Entry '{entry.name}': remote URL mismatch — "
                            f"manifest declares '{entry.remote}' but clone has '{actual_url}'"
                        )

        return result

    # ------------------------------------------------------------------
    # list
    # ------------------------------------------------------------------

    def list_bcs(self, manifest_path: Path) -> tuple[int, str]:
        """
        Return (exit_code, output) listing one BC per line.

        Format per line: <canonical-name> <remote-url>
        Exits zero if manifest is valid; non-zero on parse error.
        """
        try:
            manifest = load_manifest(manifest_path)
        except (yaml.YAMLError, ValueError) as exc:
            return 1, f"Error loading manifest: {exc}\n"

        lines = [f"{entry.name} {entry.remote}" for entry in manifest.entries]
        return 0, "\n".join(lines) + ("\n" if lines else "")

    # ------------------------------------------------------------------
    # sync
    # ------------------------------------------------------------------

    def sync(
        self,
        manifest_path: Path,
        repos_dir: Path,
    ) -> tuple[int, str]:
        """
        Sync repos_dir against the manifest.

        - Clone missing repos.
        - Skip existing repos with matching remote.
        - Warn about repos_dir entries not in manifest (but do not delete them).

        Returns (exit_code, output).
        """
        try:
            manifest = load_manifest(manifest_path)
        except (yaml.YAMLError, ValueError) as exc:
            return 1, f"Error loading manifest: {exc}\n"

        repos_dir.mkdir(parents=True, exist_ok=True)
        declared_names = {e.name for e in manifest.entries}

        lines: list[str] = []
        clone_count = 0

        for entry in manifest.entries:
            dest = repos_dir / entry.name
            if dest.exists() and dest.is_dir():
                actual_url = self._git.get_remote_url(dest)
                if actual_url == entry.remote:
                    lines.append(
                        f"Skipped '{entry.name}': already present with matching remote"
                    )
                else:
                    lines.append(
                        f"Skipped '{entry.name}': already present (remote mismatch — "
                        f"expected '{entry.remote}', found '{actual_url}')"
                    )
            else:
                self._git.clone(entry.remote, dest)
                clone_count += 1
                lines.append(f"Cloned '{entry.name}' from '{entry.remote}'")

        # Warn about extra dirs
        if repos_dir.exists():
            actual_dirs = {d.name for d in repos_dir.iterdir() if d.is_dir()}
            extra = actual_dirs - declared_names
            for name in sorted(extra):
                lines.append(f"Warning: '{name}' is present in repos dir but not declared in manifest")

        return 0, "\n".join(lines) + ("\n" if lines else "")
