"""A delete by filter on the primary key, against the live engine.

The mocked tests prove the filter string; only the engine can say it
accepts it, that the primary key is filterable once the settings are
applied, and that an id with quotes in it deletes only itself.
"""

import pytest

from meili._client import client as meili_client
from product.factories.product import ProductFactory
from product.models.product import ProductTranslation
from tests.conftest import requires_meilisearch
from tests.utils.meilisearch import LiveSearchIndex, private_index

pytestmark = pytest.mark.django_db


def _stored_ids(uid: str) -> set[str]:
    page = meili_client.get_index(uid).get_documents({"limit": 1000})
    return {str(doc.pk) for doc in page.results}


@requires_meilisearch
def test_the_engine_deletes_by_a_filter_on_the_primary_key():
    with private_index(ProductTranslation) as uid, LiveSearchIndex() as index:
        products = [
            ProductFactory(active=True, num_images=0, num_reviews=0)
            for _ in range(3)
        ]
        translations = list(
            ProductTranslation.objects.filter(
                master_id__in=[p.pk for p in products]
            ).order_by("pk")
        )
        index.add(*translations)
        stored = {str(t.pk) for t in translations}
        assert _stored_ids(uid) == stored
        doomed = sorted(stored)[:2]
        doomed_with_noise = [*doomed, 'x"] OR pk != "', "a\b"]

        task = meili_client.delete_documents_by_primary_key(
            uid, "pk", doomed_with_noise
        )
        finished = meili_client.wait_for_task(task.task_uid)

        assert finished.status == "succeeded", finished.error
        assert _stored_ids(uid) == stored - set(doomed)
