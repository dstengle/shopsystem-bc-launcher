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


# ---------------------------------------------------------------------------
# Fleet-wide provider-keyed model mapping table (lead-ifye3.2 behavior 4)
# ---------------------------------------------------------------------------
#
# This table is the fleet-wide source of the run-wide model literal: one ROW per
# provider, each naming a literal model ID for the ``coding`` / ``review`` /
# ``default`` tiers.
#
# lead-ifye3.5 behavior 5 (a3b2b6bebcee78f5, supersedes 22f2a5bda5c29044): fabro
# >= v0.267.0 removed model_stylesheet templating outright (fabro commit
# 911e080f3), so the retired per-node-class ``-I MODEL_CODING/REVIEW/DEFAULT``
# inputs — which fed the poured stylesheet's ``{{ inputs.MODEL_* }}`` placeholders
# — are GONE (the placeholder names are no longer poured or supplied).  Per
# product-authority direction, per-node-class model differentiation is
# DEPRIORITIZED in favor of a single RUN-WIDE model the engage supplies on the
# finite ``fabro run`` as ``--model <literal> --provider <active>``.  This mapping
# table (ADR-063) STAYS UNCHANGED as the lookup structure; the engage selects the
# ACTIVE provider's row's ``coding`` tier (the substantive-work tier) as the
# run-wide literal — so the operator still selects the whole per-provider model
# with the same ``--llm-provider`` override, no software release.  The row keys
# below are retained (the mapping row shape is unchanged); only the graph-side
# ``-I MODEL_*`` placeholder-input names are retired.
MODEL_TIER_CODING = "coding"
MODEL_TIER_REVIEW = "review"
MODEL_TIER_DEFAULT = "default"

PROVIDER_MODEL_MAPPING: dict[str, dict[str, str]] = {
    # Anthropic row — the DEFAULT path.  Preserves today's effective models:
    # the pre-placeholder stylesheet ran every node-class (``*`` / ``.classify``
    # / ``.coding`` / ``.review``) on ``claude-haiku-4-5``, so all three tiers
    # resolve to ``claude-haiku-4-5`` and the anthropic launch is behavior-
    # equivalent to before this behavior (incl. the lead-i0wi classify-on-haiku
    # routing).
    LLM_PROVIDER_ANTHROPIC: {
        MODEL_TIER_CODING: "claude-haiku-4-5",
        MODEL_TIER_REVIEW: "claude-haiku-4-5",
        MODEL_TIER_DEFAULT: "claude-haiku-4-5",
    },
    # OpenRouter row — literal OpenRouter model IDs (the ``anthropic/…`` slugs of
    # OpenRouter's OpenAI-compatible catalog) reached via the no-shim broker
    # credential (behavior 3).  The coding/review judgment tiers get a stronger
    # model than the default/classify tier.
    LLM_PROVIDER_OPENROUTER: {
        MODEL_TIER_CODING: "anthropic/claude-sonnet-4.5",
        MODEL_TIER_REVIEW: "anthropic/claude-sonnet-4.5",
        MODEL_TIER_DEFAULT: "anthropic/claude-haiku-4.5",
    },
}


def resolve_model_mapping(provider: str | None = None) -> dict[str, str]:
    """Resolve the provider-keyed model row (the ``coding`` / ``review`` /
    ``default`` literal model IDs) for the ACTIVE ``provider``.

    ``provider`` is the already-resolved active provider name (see
    ``resolve_llm_provider``).  An unknown / ``None`` provider falls back to the
    Anthropic row so the launch keeps today's behavior-preserving default model
    set rather than failing to resolve the node-class placeholders.
    """
    return PROVIDER_MODEL_MAPPING.get(
        provider or LLM_PROVIDER_DEFAULT, PROVIDER_MODEL_MAPPING[LLM_PROVIDER_ANTHROPIC]
    )


# The run-wide model tier (lead-ifye3.5 behavior 5 / a3b2b6bebcee78f5).  With
# per-node-class model differentiation DEPRIORITIZED (fabro >= v0.267.0 removed
# model_stylesheet templating), the launcher supplies ONE run-wide model on the
# finite ``fabro run --model``.  It is the mapping row's ``coding`` tier — the
# substantive-work tier — so the real BC work gets the provider's stronger model.
MODEL_TIER_RUN_WIDE = MODEL_TIER_CODING


def resolve_run_wide_model(provider: str | None = None) -> str:
    """The single RUN-WIDE literal model ID the launcher supplies on every finite
    ``fabro run --model`` for the ACTIVE ``provider`` (lead-ifye3.5 behavior 5 /
    a3b2b6bebcee78f5).

    fabro >= v0.267.0 removed model_stylesheet templating (fabro commit
    911e080f3), so per-node-class differentiation is deprioritized in favor of
    this one run-wide model; it is the ACTIVE provider's mapping-row ``coding``
    tier (the substantive-work tier).  Every node-class (``*`` / ``.classify`` /
    ``.coding`` / ``.review``) resolves to this single model at run time.  The
    provider-keyed mapping table (ADR-063) is unchanged as the lookup structure.
    """
    return resolve_model_mapping(provider)[MODEL_TIER_RUN_WIDE]


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
