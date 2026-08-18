from typing import Self

from meilisearch.client import Client as _Client
from meilisearch.models.task import Task
from meilisearch.task import TaskInfo

from meili._settings import _MeiliSettings
from meili.dataclasses import MeiliIndexSettings


class Client:
    def __init__(self, settings: _MeiliSettings):
        self.settings = settings  # Store settings for management commands
        base_url = (
            f"http{'s' if settings.https else ''}://"
            f"{settings.host}:{settings.port}"
        )
        # Master-key client: index/key/document administration and indexing.
        self.client = _Client(
            base_url,
            settings.master_key,
            timeout=settings.timeout,
        )
        # Read-only client for public search traffic. Authenticated with the
        # search key (falls back to the master key when none is provisioned)
        # so the master key never serves untrusted query paths.
        self.search_client = _Client(
            base_url,
            settings.search_key,
            timeout=settings.timeout,
        )
        self._base_url = base_url
        self._search_key_uid: str | None = None
        # schema -> (client, expiry-timestamp) for tenant-token clients.
        self._tenant_search_clients: dict[str, tuple[_Client, float]] = {}
        self.is_sync = settings.sync
        self.tasks: list[Task | TaskInfo] = []

    # Tenant tokens live 1 hour; clients are re-minted after 50 minutes
    # so a request never rides a token that expires mid-flight.
    _TENANT_TOKEN_TTL_SECONDS = 3600
    _TENANT_CLIENT_REFRESH_SECONDS = 3000

    def search_client_for_schema(self, schema_name: str) -> _Client:
        """Read-only client whose key is SCOPED to one tenant's indexes.

        Uses Meilisearch tenant tokens: a JWT minted from the search
        API key with search rules ``{"{schema}__*": {}}``, so even a
        tenant-context bug in query-building code cannot read another
        tenant's indexes — the engine itself refuses.

        Fallbacks (both documented, not silent):
        - public schema → the unscoped search client (platform admin
          surfaces search across shared data only).
        - no dedicated search key provisioned (dev/CI, where the search
          key falls back to the master key — tenant tokens cannot be
          minted from a master key) → the unscoped search client.
        """
        if not schema_name or schema_name == "public":
            return self.search_client
        if self.settings.search_key == self.settings.master_key:
            return self.search_client

        # OFFLINE mode (tests/CI without a live engine): minting a
        # tenant token needs a key-metadata round trip — serve the
        # unscoped client instead of dialing a server that isn't there.
        from django.conf import settings as django_settings

        if django_settings.MEILISEARCH.get("OFFLINE", False):
            return self.search_client

        import time

        cached = self._tenant_search_clients.get(schema_name)
        if cached is not None and cached[1] > time.monotonic():
            return cached[0]

        from datetime import datetime, timedelta, timezone

        if self._search_key_uid is None:
            # The /keys/{key} endpoint accepts the key value itself and
            # needs key-management rights — use the master client once.
            self._search_key_uid = self.client.get_key(
                self.settings.search_key
            ).uid

        token = self.client.generate_tenant_token(
            self._search_key_uid,
            {f"{schema_name}__*": {}},
            api_key=self.settings.search_key,
            expires_at=datetime.now(tz=timezone.utc)
            + timedelta(seconds=self._TENANT_TOKEN_TTL_SECONDS),
        )
        tenant_client = _Client(
            self._base_url, token, timeout=self.settings.timeout
        )
        self._tenant_search_clients[schema_name] = (
            tenant_client,
            time.monotonic() + self._TENANT_CLIENT_REFRESH_SECONDS,
        )
        return tenant_client

    def flush_tasks(self):
        self.tasks = []

    def with_settings(
        self, index_name: str, index_settings: MeiliIndexSettings
    ):
        settings_payload = {
            "displayedAttributes": index_settings.displayed_fields or ["*"],
            "searchableAttributes": index_settings.searchable_fields or ["*"],
            "filterableAttributes": index_settings.filterable_fields or [],
            "sortableAttributes": index_settings.sortable_fields or [],
            "rankingRules": (
                index_settings.ranking_rules
                or [
                    "words",
                    "typo",
                    "proximity",
                    "attribute",
                    "sort",
                    "exactness",
                ]
            ),
            "stopWords": index_settings.stop_words or [],
            "synonyms": index_settings.synonyms or {},
            "distinctAttribute": index_settings.distinct_attribute,
            "typoTolerance": (
                index_settings.typo_tolerance
                or {
                    "enabled": True,
                    "minWordSizeForTypos": {"oneTypo": 5, "twoTypos": 9},
                    "disableOnWords": [],
                    "disableOnAttributes": [],
                }
            ),
            "faceting": index_settings.faceting or {"maxValuesPerFacet": 100},
            "pagination": index_settings.pagination or {"maxTotalHits": 1000},
        }

        # Add searchCutoffMs if provided
        if index_settings.search_cutoff_ms is not None:
            settings_payload["searchCutoffMs"] = index_settings.search_cutoff_ms

        self.tasks.append(
            self._handle_sync(
                self.client.index(index_name).update_settings(settings_payload)
            )
        )
        return self

    def create_index(self, index_name: str, primary_key: str):
        existing = {i.uid: i for i in self.get_indexes()}
        if index_name not in existing:
            self.tasks.append(
                self._handle_sync(
                    self.client.create_index(
                        index_name, {"primaryKey": primary_key}
                    )
                )
            )
        elif existing[index_name].primary_key is None:
            # Self-heal an index that was auto-created by a settings task
            # (e.g. ``meilisearch_apply_settings`` running before any sync
            # on a brand-new tenant): such indexes have no primaryKey, and
            # every document addition then fails with
            # ``index_primary_key_multiple_candidates_found``. The engine
            # allows setting the primaryKey in place while the index is
            # empty — which it must be, since additions were rejected.
            self.tasks.append(
                self._handle_sync(
                    self.client.index(index_name).update(
                        primary_key=primary_key
                    )
                )
            )
        return self

    def get_index(self, index_name: str):
        return self.client.index(index_name)

    def get_search_index(self, index_name: str):
        """Return an index handle bound to the read-only search client."""
        return self.search_client.index(index_name)

    def wait_for_task(
        self,
        task_uid: int,
        *,
        timeout_in_ms: int | None = None,
        interval_in_ms: int = 50,
    ) -> Task | TaskInfo:
        if timeout_in_ms is not None:
            task = self.client.wait_for_task(
                task_uid,
                timeout_in_ms=timeout_in_ms,
                interval_in_ms=interval_in_ms,
            )
        else:
            task = self.client.wait_for_task(task_uid)
        return self._handle_sync(task)

    def get_indexes(self):
        return self.client.get_indexes()["results"]

    def update_display(self, index_name: str, attributes: list | None) -> Self:
        if attributes is None:
            return self
        self._handle_sync(
            self.client.index(index_name).update_displayed_attributes(
                attributes
            )
        )
        return self

    def update_searchable(
        self, index_name: str, attributes: list | None
    ) -> Self:
        if attributes is None:
            return self
        self._handle_sync(
            self.client.index(index_name).update_searchable_attributes(
                attributes
            )
        )
        return self

    def update_filterable(
        self, index_name: str, attributes: list | None
    ) -> Self:
        if attributes is None:
            return self
        self._handle_sync(
            self.client.index(index_name).update_filterable_attributes(
                attributes
            )
        )
        return self

    def update_sortable(self, index_name: str, attributes: list | None) -> Self:
        if attributes is None:
            return self
        self._handle_sync(
            self.client.index(index_name).update_sortable_attributes(attributes)
        )
        return self

    def _handle_sync(self, task: Task | TaskInfo) -> Task | TaskInfo:
        if self.is_sync:
            if hasattr(task, "task_uid"):
                uid = task.task_uid
            elif hasattr(task, "uid"):
                uid = task.uid
            else:
                raise AttributeError("Task object has no uid attribute")

            task = self.client.wait_for_task(uid)
            if task.status == "failed":
                raise Exception(task.error)
        return task


client = Client(_MeiliSettings.from_settings())
