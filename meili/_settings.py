from dataclasses import dataclass
from typing import TypedDict, cast


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
    DEFAULT_BATCH_SIZE: int


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
    batch_size: int

    @classmethod
    def from_settings(cls) -> "_MeiliSettings":
        from django.conf import settings  # noqa: PLC0415

        meili_settings = cast("MeiliSettings", settings.MEILISEARCH)

        master_key = meili_settings.get("MASTER_KEY")
        if not master_key:
            raise ValueError("MEILISEARCH['MASTER_KEY'] is required")

        debug = meili_settings.get("DEBUG")
        sync = meili_settings.get("SYNC")
        offline = meili_settings.get("OFFLINE")

        return cls(
            https=meili_settings.get("HTTPS", False),
            host=meili_settings.get("HOST", "localhost"),
            master_key=master_key,
            port=meili_settings.get("PORT", 7700),
            timeout=meili_settings.get("TIMEOUT", None),
            client_agents=meili_settings.get("CLIENT_AGENTS", None),
            debug=settings.DEBUG if debug is None else debug,
            sync=False if sync is None else sync,
            offline=False if offline is None else offline,
            batch_size=meili_settings.get("DEFAULT_BATCH_SIZE", 1000),
        )
