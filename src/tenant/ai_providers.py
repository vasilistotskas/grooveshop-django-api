"""The OpenAI-compatible endpoint of each AI provider a store can choose.

A store picks its provider from ``AIProvider``, never by typing an
address: the agent gateway dials the base URL server-side, so a URL a
store could type would be an SSRF vector (the same reason
``core.net.safe_fetch`` exists). The base URLs end with ``/`` so the
gateway's OpenAI client resolves ``chat/completions`` against them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tenant.enum import AIProvider

if TYPE_CHECKING:
    from tenant.models.tenant import Tenant


AI_PROVIDER_BASE_URLS: dict[AIProvider, str] = {
    AIProvider.GEMINI: "https://generativelanguage.googleapis.com/v1beta/openai/",
    AIProvider.OPENAI: "https://api.openai.com/v1/",
    AIProvider.ANTHROPIC: "https://api.anthropic.com/v1/",
    AIProvider.MISTRAL: "https://api.mistral.ai/v1/",
    AIProvider.GROQ: "https://api.groq.com/openai/v1/",
    AIProvider.OPENROUTER: "https://openrouter.ai/api/v1/",
}


def serialize_ai_profile(tenant: Tenant) -> dict[str, Any] | None:
    """The store's AI profile for the agent gateway, or ``None``.

    ``validate_ai_profile`` keeps the three fields set together or empty,
    so an empty provider means the store has no AI features: there is no
    platform fallback.
    """
    if not tenant.ai_provider:
        return None
    return {
        "provider": tenant.ai_provider,
        "base_url": AI_PROVIDER_BASE_URLS[AIProvider(tenant.ai_provider)],
        "model": tenant.ai_model,
        "api_key": tenant.ai_api_key,
        "reasoning_effort": tenant.ai_reasoning_effort,
    }
