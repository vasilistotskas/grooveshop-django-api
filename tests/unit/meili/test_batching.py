"""Index documents go out in batches, and a writer can flush them once.

``reindex_products_by_pk`` used to queue one ``meili.index_document`` task
per translation; ``meili.index_documents`` takes a batch and makes one add
and one delete call. ``collect_reindex`` holds every document the block
touches and queues them once on exit.
"""

from __future__ import annotations

import math
from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings

from meili.batching import collect_reindex
from meili.tasks import index_documents_task
from product.factories.product import ProductFactory
from product.models.product import ProductTranslation
from product.signals import reindex_products_by_pk

pytestmark = pytest.mark.django_db

ONLINE = {"OFFLINE": False, "ASYNC_INDEXING": True}
APPLY_ASYNC = "meili.tasks.index_documents_task.apply_async"


def _pks(dispatch) -> list[list[int]]:
    return [call.kwargs["kwargs"]["pks"] for call in dispatch.call_args_list]


def test_n_documents_queue_ceil_n_over_batch_tasks(
    django_capture_on_commit_callbacks,
):
    products = [
        ProductFactory(active=True, num_images=0, num_reviews=0)
        for _ in range(4)
    ]
    expected = set(
        ProductTranslation.objects.filter(
            master_id__in=[p.pk for p in products]
        ).values_list("pk", flat=True)
    )
    batch = 3

    with (
        override_settings(MEILISEARCH=ONLINE),
        patch("meili.batching.INDEX_TASK_BATCH_SIZE", batch),
        patch(APPLY_ASYNC) as dispatch,
        django_capture_on_commit_callbacks(execute=True),
    ):
        count = reindex_products_by_pk([p.pk for p in products])

    assert count == len(expected)
    batches = _pks(dispatch)
    assert len(batches) == math.ceil(len(expected) / batch)
    assert all(len(pks) <= batch for pks in batches)
    assert {pk for pks in batches for pk in pks} == expected


@override_settings(DEBUG=False)
def test_collect_reindex_queues_everything_once_on_exit(
    django_capture_on_commit_callbacks,
):
    first = ProductFactory(active=True, num_images=0, num_reviews=0)
    second = ProductFactory(active=True, num_images=0, num_reviews=0)
    expected = set(
        ProductTranslation.objects.filter(
            master_id__in=[first.pk, second.pk]
        ).values_list("pk", flat=True)
    )

    with (
        override_settings(MEILISEARCH=ONLINE),
        patch(APPLY_ASYNC) as batched,
        patch("meili.tasks.index_document_task.apply_async") as single,
        django_capture_on_commit_callbacks(execute=True),
    ):
        with collect_reindex():
            # A bulk helper, a save signal and the same product again:
            # all of it is held back until the block closes.
            assert reindex_products_by_pk([first.pk]) == 0
            first.save()
            second.save()
            reindex_products_by_pk([first.pk, second.pk])
            batched.assert_not_called()
            single.assert_not_called()

    assert batched.call_count == 1
    single.assert_not_called()
    assert _pks(batched) == [sorted(expected)]


def test_collect_reindex_flushes_when_the_block_raises(
    django_capture_on_commit_callbacks,
):
    product = ProductFactory(active=True, num_images=0, num_reviews=0)

    with (
        override_settings(MEILISEARCH=ONLINE),
        patch(APPLY_ASYNC) as dispatch,
        django_capture_on_commit_callbacks(execute=True),
        pytest.raises(RuntimeError),
        collect_reindex(),
    ):
        reindex_products_by_pk([product.pk])
        raise RuntimeError("boom")

    assert dispatch.call_count == 1


def test_a_nested_collect_reindex_leaves_the_flush_to_the_outer_block(
    django_capture_on_commit_callbacks,
):
    product = ProductFactory(active=True, num_images=0, num_reviews=0)

    with (
        override_settings(MEILISEARCH=ONLINE),
        patch(APPLY_ASYNC) as dispatch,
        django_capture_on_commit_callbacks(execute=True),
    ):
        with collect_reindex():
            with collect_reindex():
                reindex_products_by_pk([product.pk])
            dispatch.assert_not_called()

    assert dispatch.call_count == 1


def test_outside_collect_reindex_nothing_is_held_back(
    django_capture_on_commit_callbacks,
):
    product = ProductFactory(active=True, num_images=0, num_reviews=0)

    with (
        override_settings(MEILISEARCH=ONLINE),
        patch(APPLY_ASYNC) as dispatch,
        django_capture_on_commit_callbacks(execute=True),
    ):
        with collect_reindex():
            pass
        reindex_products_by_pk([product.pk])

    assert dispatch.call_count == 1


class TestIndexDocumentsTask:
    def _client(self):
        mock_index = MagicMock()
        mock_index.add_documents.return_value = MagicMock(task_uid=1)
        mock_client = MagicMock()
        mock_client.delete_documents_by_primary_key.return_value = MagicMock(
            task_uid=2
        )
        mock_client.get_index.return_value = mock_index
        mock_client.wait_for_task.return_value = MagicMock(status="succeeded")
        return mock_client, mock_index

    def test_one_add_and_one_delete_call_per_batch(self):
        live = [
            ProductFactory(active=True, num_images=0, num_reviews=0)
            for _ in range(2)
        ]
        dead = ProductFactory(active=False, num_images=0, num_reviews=0)
        pks = list(
            ProductTranslation.objects.filter(
                master_id__in=[p.pk for p in [*live, dead]]
            ).values_list("pk", flat=True)
        )
        live_pks = {
            str(pk)
            for pk in ProductTranslation.objects.filter(
                master_id__in=[p.pk for p in live]
            ).values_list("pk", flat=True)
        }
        dead_pks = {
            str(pk)
            for pk in ProductTranslation.objects.filter(
                master=dead
            ).values_list("pk", flat=True)
        }
        mock_client, mock_index = self._client()

        with patch("meili._client.client", mock_client):
            result = index_documents_task(
                "product", "producttranslation", [*pks, 10**9]
            )

        mock_index.add_documents.assert_called_once()
        added = mock_index.add_documents.call_args.args[0]
        assert {doc["id"] for doc in added} == live_pks
        mock_client.delete_documents_by_primary_key.assert_called_once()
        _name, primary_key, ids = (
            mock_client.delete_documents_by_primary_key.call_args.args
        )
        assert primary_key == "pk"
        assert set(ids) == dead_pks
        # The deprecated delete-by-ids call is gone.
        mock_index.delete_documents.assert_not_called()
        assert result == {
            "status": "success",
            "added": len(live_pks),
            "removed": len(dead_pks),
            "skipped": 1,
        }

    def test_no_delete_call_when_everything_qualifies(self):
        product = ProductFactory(active=True, num_images=0, num_reviews=0)
        pks = list(
            ProductTranslation.objects.filter(master=product).values_list(
                "pk", flat=True
            )
        )
        mock_client, mock_index = self._client()

        with patch("meili._client.client", mock_client):
            index_documents_task("product", "producttranslation", pks)

        mock_index.add_documents.assert_called_once()
        mock_client.delete_documents_by_primary_key.assert_not_called()

    def test_a_failed_engine_task_is_not_retried(self):
        from meili.exceptions import MeiliTaskFailed

        product = ProductFactory(active=True, num_images=0, num_reviews=0)
        pks = list(
            ProductTranslation.objects.filter(master=product).values_list(
                "pk", flat=True
            )
        )
        mock_client, _index = self._client()
        mock_client.wait_for_task.return_value = MagicMock(
            status="failed", error="bad"
        )

        with (
            patch("meili._client.client", mock_client),
            pytest.raises(MeiliTaskFailed),
        ):
            index_documents_task("product", "producttranslation", pks)

    @pytest.mark.parametrize(
        "add_failure",
        [
            MagicMock(status="failed", error="bad document"),
            TimeoutError("engine busy"),
        ],
    )
    def test_the_delete_is_submitted_even_when_the_add_fails(self, add_failure):
        from meili.exceptions import MeiliTaskFailed

        live = ProductFactory(active=True, num_images=0, num_reviews=0)
        dead = ProductFactory(active=False, num_images=0, num_reviews=0)
        pks = list(
            ProductTranslation.objects.filter(
                master_id__in=[live.pk, dead.pk]
            ).values_list("pk", flat=True)
        )
        mock_client, _mock_index = self._client()
        outcomes = [add_failure, MagicMock(status="succeeded")]

        def wait(task_uid):
            outcome = outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        mock_client.wait_for_task.side_effect = wait

        with (
            patch("meili._client.client", mock_client),
            pytest.raises((MeiliTaskFailed, TimeoutError)),
        ):
            index_documents_task("product", "producttranslation", pks)

        mock_client.delete_documents_by_primary_key.assert_called_once()
        assert mock_client.wait_for_task.call_count == 2


def test_documents_are_queued_in_the_schema_they_were_collected_in():
    from contextlib import contextmanager

    from meili.batching import collect_if_active

    active: list[str] = []
    queued: list[tuple[str | None, list[int]]] = []

    @contextmanager
    def fake_schema_context(schema):
        active.append(schema)
        try:
            yield
        finally:
            active.pop()

    def fake_dispatch(task, kwargs):
        queued.append((active[-1] if active else None, kwargs["pks"]))

    schemas = iter(["store_a", "store_b", "store_a"])
    with (
        patch("meili.batching._current_schema", lambda: next(schemas)),
        patch("meili.batching.schema_context", fake_schema_context),
        patch("meili.batching.dispatch_on_commit", fake_dispatch),
    ):
        with collect_reindex():
            collect_if_active("product", "producttranslation", [1])
            collect_if_active("product", "producttranslation", [1])
            collect_if_active("product", "producttranslation", [2])
        # The block ended outside every schema, yet each id went out in
        # its own: the same pk in two stores is two documents.
        assert active == []

    assert sorted(queued) == [("store_a", [1, 2]), ("store_b", [1])]
