"""Launch-time active-LLM-provider resolution (lead-ifye3.2, behavior 1).

Introduces the "active LLM provider" concept the fabro launch path threads
into the container's fabro run.  Today Anthropic (via the in-container
anthropic-oauth-shim) is the ONLY provider the launcher wires; this module
turns the provider into a RESOLVED value with an Anthropic DEFAULT so a later
launch-time override (``--llm-provider`` / ``BCLAUNCHER_LLM_PROVIDER``) can
select a different provider (openrouter — behaviors 2-5) without re-plumbing
the engage.

Behavior 1 pins only the DEFAULT: a plain launch with no operator-supplied
override resolves to ``"anthropic"``.  The resolution reads the two override
sources the scenario names (the explicit ``--llm-provider`` value and the
``BCLAUNCHER_LLM_PROVIDER`` environment variable) so behaviors 2-5 extend the
ACCEPTED provider values here rather than re-deriving the precedence.
"""
from __future__ import annotations

from collections.abc import Mapping

# The Anthropic-subscription provider (the resolution DEFAULT).
LLM_PROVIDER_ANTHROPIC = "anthropic"
LLM_PROVIDER_DEFAULT = LLM_PROVIDER_ANTHROPIC

# The OpenRouter provider — the first launch-time override target (behaviors
# 2-3).  On this provider the launcher wires a NO-SHIM agent-vault-brokered
# credential (OPENROUTER_API_KEY=__PLACEHOLDER__ node-side, the broker's MITM
# proxy substitutes the real key on the wire — mirroring the GITHUB_TOKEN
# no-shim pattern), NOT the Anthropic anthropic-oauth-shim header-reshaping path.
LLM_PROVIDER_OPENROUTER = "openrouter"

# The launch-time environment override the operator can set to pick the active
# provider (mirrors the ``--llm-provider`` flag).  Behavior 2 threads the flag
# down; both feed this same resolution.
BCLAUNCHER_LLM_PROVIDER_ENV = "BCLAUNCHER_LLM_PROVIDER"


def resolve_llm_provider(
    override: str | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    """Resolve the ACTIVE LLM provider for a launch.

    Precedence (behaviors 2-5 extend the accepted values, not the order):

      1. an explicit launch-time ``--llm-provider`` override, when supplied;
      2. the ``BCLAUNCHER_LLM_PROVIDER`` environment override, when set;
      3. the Anthropic DEFAULT.

    A plain launch — no ``--llm-provider`` override and no
    ``BCLAUNCHER_LLM_PROVIDER`` in ``env`` — resolves to ``"anthropic"``.
    Values are normalized (trimmed + lowercased) so ``Anthropic`` /
    ``ANTHROPIC`` resolve to the same canonical provider name.
    """
    if override is not None and override.strip():
        return override.strip().lower()
    if env is not None:
        env_override = env.get(BCLAUNCHER_LLM_PROVIDER_ENV)
        if env_override is not None and env_override.strip():
            return env_override.strip().lower()
    return LLM_PROVIDER_DEFAULT
