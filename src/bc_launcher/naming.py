"""Container name, product slug, and beads issue-prefix helpers.

Extracted verbatim from ``controller`` (Phase 1 of the controller.py
decomposition). Leaf module; re-exported by ``controller`` for import-path
compatibility. Do not import ``controller`` from here (would cycle).
"""
from __future__ import annotations
import re



def _container_name(bc_name: str) -> str:
    return f"bc-{bc_name}"



def beads_prefix_for(bc_name: str) -> str:
    """Derive a *fallback* beads issue_prefix from the BC name.

    A BC named ``shopsystem-<identifier>`` would, by name-derivation, carry a
    prefix derived from the identifier: lowercase, non-alphanumerics stripped,
    the ``shopsystem-`` namespace prefix dropped (e.g. ``shopsystem-messaging``
    → ``messaging``).

    NOTE — name-derivation is NOT authoritative (lead-rply).  A cloned repo's
    committed registry may carry a DIFFERENT prefix than the BC name implies
    (e.g. ``shopsystem-bc-launcher`` name-derives ``bclauncher`` but its
    committed registry uses ``bclaunch``; ``shopsystem-templates`` name-derives
    ``templates`` but uses ``tmpl``).  The launcher MUST adopt the COMMITTED
    prefix the cloned repo already carries — see
    ``_committed_beads_prefix`` — and only fall back to this name-derived value
    when the clone carries no committed registry from which a prefix can be
    read.
    """
    ident = bc_name
    if ident.startswith("shopsystem-"):
        ident = ident[len("shopsystem-"):]
    ident = re.sub(r"[^a-z0-9]", "", ident.lower())
    return ident



# Issue ids in a beads registry are ``<prefix>-<suffix>`` where the suffix is a
# short base36-ish token (e.g. ``bclaunch-eaa``).  The committed prefix is the
# segment before the FINAL hyphen of an issue id.
_BEADS_ISSUE_ID_RE = re.compile(r'"id"\s*:\s*"(?P<id>[^"]+)"')



def committed_beads_prefix_from_registry(registry_text: str) -> str | None:
    """Extract the committed issue_prefix from a ``.beads/issues.jsonl`` blob.

    The committed registry is JSONL: one issue object per line, each carrying an
    ``"id":"<prefix>-<suffix>"`` field.  The committed prefix is the portion of
    the first issue id up to (but excluding) its final hyphen.  Returns ``None``
    when the blob carries no parseable issue id (e.g. an empty registry), so the
    caller can fall back to name-derivation rather than configuring an empty
    prefix.
    """
    for match in _BEADS_ISSUE_ID_RE.finditer(registry_text or ""):
        issue_id = match.group("id")
        if "-" in issue_id:
            return issue_id.rsplit("-", 1)[0]
    return None



def _slugify(text: str) -> str:
    """Lowercase and replace runs of spaces with hyphens."""
    return re.sub(r"\s+", "-", text.strip().lower())
