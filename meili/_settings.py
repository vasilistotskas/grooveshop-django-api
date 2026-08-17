from dataclasses import dataclass
from typing import TypedDict, cast


class MeiliSettings(TypedDict):
    HTTPS: bool
    HOST: str
    MASTER_KEY: str
    SEARCH_KEY: str
    PORT: int
    TIMEOUT: int | None
    SYNC: bool | None
    OFFLINE: bool | None
    ASYNC_INDEXING: bool | None
    DEFAULT_BATCH_SIZE: int


@dataclass(frozen=True, slots=True)
class _MeiliSettings:
    https: bool
    host: str
    master_key: str
    search_key: str
    port: int
    timeout: int | None
    sync: bool

    @classmethod
    def from_settings(cls) -> "_MeiliSettings":
        from django.conf import settings  # noqa: PLC0415

        meili_settings = cast("MeiliSettings", settings.MEILISEARCH)

        master_key = meili_settings.get("MASTER_KEY")
        if not master_key:
            raise ValueError("MEILISEARCH['MASTER_KEY'] is required")

        # Fall back to the master key when no dedicated search key is
        # provisioned so local/dev/test keep working; production should set
        # MEILI_SEARCH_KEY to a read-only search key.
        search_key = meili_settings.get("SEARCH_KEY") or master_key

        sync = meili_settings.get("SYNC")

        return cls(
            https=meili_settings.get("HTTPS", False),
            host=meili_settings.get("HOST", "localhost"),
            master_key=master_key,
            search_key=search_key,
            port=meili_settings.get("PORT", 7700),
            timeout=meili_settings.get("TIMEOUT", None),
            sync=False if sync is None else sync,
        )
