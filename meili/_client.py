from typing import Optional

from meilisearch.client import Client as _Client
from meilisearch.models.task import Task
from meilisearch.task import TaskInfo

from meili._settings import _MeiliSettings


class Client:
    def __init__(self, settings: _MeiliSettings):
        self.client = _Client(
            f"http{'s' if settings.https else ''}://{settings.host}:{settings.port}",
            settings.master_key,
            timeout=settings.timeout,
            client_agents=settings.client_agents,
        )
        self.is_sync = settings.sync
        self.tasks = []

    def flush_tasks(self):
        self.tasks = []

    def with_settings(
        self,
        index_name: str,
        displayed_fields: Optional[list[str]] = None,
        searchable_fields: Optional[list[str]] = None,
        filterable_fields: Optional[list[str]] = None,
        sortable_fields: Optional[list[str]] = None,
        ranking_rules: Optional[list[str]] = None,
        stop_words: Optional[list[str]] = None,
        synonyms: Optional[dict[str, list[str]]] = None,
        distinct_attribute: Optional[str] = None,
        typo_tolerance: Optional[dict[str, any]] = None,
        faceting: Optional[dict[str, any]] = None,
        pagination: Optional[dict[str, any]] = None,
    ):
        settings_payload = {
            "displayedAttributes": displayed_fields or ["*"],
            "searchableAttributes": searchable_fields or ["*"],
            "filterableAttributes": filterable_fields or [],
            "sortableAttributes": sortable_fields or [],
            "rankingRules": ranking_rules
            or ["words", "typo", "proximity", "attribute", "sort", "exactness"],
            "stopWords": stop_words or [],
            "synonyms": synonyms or {},
            "distinctAttribute": distinct_attribute,
            "typoTolerance": typo_tolerance
            or {
                "enabled": True,
                "minWordSizeForTypos": {"oneTypo": 5, "twoTypos": 9},
                "disableOnWords": [],
                "disableOnAttributes": [],
            },
            "faceting": faceting or {"maxValuesPerFacet": 100},
            "pagination": pagination or {"maxTotalHits": 1000},
        }

        self.tasks.append(
            self._handle_sync(self.client.index(index_name).update_settings(settings_payload))
        )
        return self

    def create_index(self, index_name: str, primary_key: str):
        if index_name not in [i.uid for i in self.get_indexes()]:
            self.tasks.append(
                self._handle_sync(self.client.create_index(index_name, {"primaryKey": primary_key}))
            )
        return self

    def get_index(self, index_name: str):
        return self.client.index(index_name)

    def wait_for_task(self, task_uid: int) -> Task | TaskInfo:
        task = self.client.wait_for_task(task_uid)
        return self._handle_sync(task)

    def get_indexes(self):
        return self.client.get_indexes()["results"]

    def _handle_sync(self, task: Task | TaskInfo) -> Task | TaskInfo:
        if self.is_sync:
            task = self.client.wait_for_task(task.task_uid)
            if task.status == "failed":
                raise Exception(task.error)
        return task


client = Client(_MeiliSettings.from_settings())
