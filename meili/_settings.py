from dataclasses import dataclass
from typing import TypedDict


class MeiliSettings(TypedDict):
    HTTPS: bool
    HOST: str
    MASTER_KEY: str
    PORT: int
    TIMEOUT: int | None
    CLIENT_AGENTS: tuple[str] | None
    DEBUG: bool | None
    SYNC: bool | None
    OFFLINE: bool | None


@dataclass(frozen=True, slots=True)
class _MeiliSettings:
    https: bool
    host: str
    master_key: str
    port: int
    timeout: int | None
    client_agents: tuple[str] | None
    debug: bool
    sync: bool
    offline: bool

    @classmethod
    def from_settings(cls) -> "_MeiliSettings":
        from django.conf import settings

        return cls(
            https=settings.MEILISEARCH.get("HTTPS", False),
            host=settings.MEILISEARCH.get("HOST", "localhost"),
            master_key=settings.MEILISEARCH.get("MASTER_KEY", None),
            port=settings.MEILISEARCH.get("PORT", 7700),
            timeout=settings.MEILISEARCH.get("TIMEOUT", None),
            client_agents=settings.MEILISEARCH.get("CLIENT_AGENTS", None),
            debug=settings.MEILISEARCH.get("DEBUG", settings.DEBUG),
            sync=settings.MEILISEARCH.get("SYNC", False),
            offline=settings.MEILISEARCH.get("OFFLINE", False),
        )
