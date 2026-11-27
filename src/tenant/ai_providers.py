"""Platform-maintained catalog of OpenAI-compatible AI providers.

A store chooses its provider from this fixed list — never by typing an
arbitrary base URL. Letting a tenant supply an endpoint address that the
agent gateway dials server-side would be an SSRF vector (the same reason
``core.net.safe_fetch`` exists here).

The catalog carries each provider's canonical OpenAI-compatible base URL
and its capabilities (tool calling and structured JSON-schema outputs) so
Django can pass the resolved profile to the agent gateway on the
internal-token-gated ``tenant/resolve`` response.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from django_stubs_ext import StrOrPromise

from tenant.enum import AIProvider

if TYPE_CHECKING:
    from tenant.models.tenant import Tenant


@dataclass(frozen=True, slots=True)
class AIProviderSpec:
    """One platform-approved OpenAI-compatible AI provider."""

    code: AIProvider
    label: StrOrPromise
    base_url: str
    supports_tool_calling: bool
    supports_structured_outputs: bool


#: Canonical OpenAI-compatible base URLs and capabilities per provider.
#: Every base URL uses HTTPS and ends with ``/`` so the gateway's
#: ``openai-go`` client resolves ``chat/completions`` cleanly.
AI_PROVIDERS: dict[AIProvider, AIProviderSpec] = {
    AIProvider.GEMINI: AIProviderSpec(
        code=AIProvider.GEMINI,
        label=AIProvider.GEMINI.label,
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        supports_tool_calling=True,
        supports_structured_outputs=True,
    ),
    AIProvider.OPENAI: AIProviderSpec(
        code=AIProvider.OPENAI,
        label=AIProvider.OPENAI.label,
        base_url="https://api.openai.com/v1/",
        supports_tool_calling=True,
        supports_structured_outputs=True,
    ),
    AIProvider.ANTHROPIC: AIProviderSpec(
        code=AIProvider.ANTHROPIC,
        label=AIProvider.ANTHROPIC.label,
        base_url="https://api.anthropic.com/v1/",
        supports_tool_calling=True,
        supports_structured_outputs=True,
    ),
    AIProvider.MISTRAL: AIProviderSpec(
        code=AIProvider.MISTRAL,
        label=AIProvider.MISTRAL.label,
        base_url="https://api.mistral.ai/v1/",
        supports_tool_calling=True,
        supports_structured_outputs=True,
    ),
    AIProvider.GROQ: AIProviderSpec(
        code=AIProvider.GROQ,
        label=AIProvider.GROQ.label,
        base_url="https://api.groq.com/openai/v1/",
        supports_tool_calling=True,
        supports_structured_outputs=True,
    ),
    AIProvider.OPENROUTER: AIProviderSpec(
        code=AIProvider.OPENROUTER,
        label=AIProvider.OPENROUTER.label,
        base_url="https://openrouter.ai/api/v1/",
        supports_tool_calling=True,
        supports_structured_outputs=True,
    ),
}

#: What the platform's gateway ran before per-store AI profiles existed
#: (``agent-gateway-config.yaml``). Used ONLY by the one-time backfill
#: migration that moves existing ``Tenant.chat_api_key`` rows into the
#: per-store profile — never as a runtime fallback for stores without one.
LEGACY_CHAT_DEFAULT_PROVIDER = AIProvider.GEMINI
LEGACY_CHAT_DEFAULT_MODEL = "gemini-3.5-flash-lite"


def get_ai_provider(code: str) -> AIProviderSpec | None:
    """Return the platform spec for *code*, or ``None`` if unknown."""
    if not code:
        return None
    try:
        provider = AIProvider(code)
    except ValueError:
        return None
    return AI_PROVIDERS.get(provider)


def serialize_ai_profile(tenant: Tenant) -> dict[str, Any] | None:
    """Internal-only gateway payload for *tenant*'s AI profile.

    Returns ``None`` unless the store has a complete, platform-approved
    profile (provider, model and API key). There is no platform fallback:
    a store without a complete profile has no AI features.
    """
    if not (tenant.ai_provider and tenant.ai_model and tenant.ai_api_key):
        return None
    spec = get_ai_provider(tenant.ai_provider)
    if spec is None:
        return None
    return {
        "provider": spec.code.value,
        "base_url": spec.base_url,
        "model": tenant.ai_model,
        "api_key": tenant.ai_api_key,
        "supports_tool_calling": spec.supports_tool_calling,
        "supports_structured_outputs": spec.supports_structured_outputs,
    }


def legacy_chat_api_key(tenant: Tenant) -> str:
    """Expand-release bridge for the ``chatApiKey`` wire field.

    Older gateway pods (before per-store AI profiles) still dial Gemini's
    platform ``CHAT_BASE_URL`` with ``chatApiKey``. Serve the profile's
    key there only when the store's profile targets Gemini, so a key for
    another provider is never sent to Google; fall back to the legacy
    ``chat_api_key`` column only while no profile is configured.
    """
    if tenant.ai_provider or tenant.ai_model or tenant.ai_api_key:
        if tenant.ai_provider == AIProvider.GEMINI and tenant.ai_api_key:
            return tenant.ai_api_key
        return ""
    return tenant.chat_api_key or ""
