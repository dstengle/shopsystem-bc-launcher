"""ManifestController — manifest validation / add / register operations.

Split from bc_launcher/manifest.py; re-exported via bc_launcher.manifest for
import-path compatibility.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

from bc_launcher.manifest import (
    DEFAULT_PRODUCT_SLUG,
    GITHUB_URL_RE,
    REQUIRED_FIELDS,
    RealGitDriver,
    RealGitHubDriver,
    ValidationResult,
    bc_name_re_for_slug,
    load_manifest,
    resolve_product_slug,
)




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
