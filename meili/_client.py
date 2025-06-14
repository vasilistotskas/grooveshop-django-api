from typing import Self

from meilisearch.client import Client as _Client
from meilisearch.models.task import Task
from meilisearch.task import TaskInfo

from meili._settings import _MeiliSettings
from meili.dataclasses import MeiliIndexSettings


class Client:
    def __init__(self, settings: _MeiliSettings):
        self.client = _Client(
            f"http{'s' if settings.https else ''}://{settings.host}:{settings.port}",
            settings.master_key,
            timeout=settings.timeout,
            client_agents=settings.client_agents,
        )
        self.is_sync = settings.sync
        self.tasks: list[Task | TaskInfo] = []

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

        self.tasks.append(
            self._handle_sync(
                self.client.index(index_name).update_settings(settings_payload)
            )
        )
        return self

    def create_index(self, index_name: str, primary_key: str):
        if index_name not in [i.uid for i in self.get_indexes()]:
            self.tasks.append(
                self._handle_sync(
                    self.client.create_index(
                        index_name, {"primaryKey": primary_key}
                    )
                )
            )
        return self

    def get_index(self, index_name: str):
        return self.client.index(index_name)

    def wait_for_task(self, task_uid: int) -> Task | TaskInfo:
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
