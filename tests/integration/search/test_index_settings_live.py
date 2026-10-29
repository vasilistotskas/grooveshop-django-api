"""The settings the live engine reports are the settings we sent.

``update_meili_settings`` PATCHes the whole payload; the engine
normalises what it stores (a granular entry gains its defaults), so a
payload the engine quietly reshaped or ignored would pass every mocked
test. This reads the index back.
"""

import pytest

from blog.models.post import BlogPostTranslation
from meili._client import client as meili_client
from product.models.product import ProductTranslation
from tests.conftest import requires_meilisearch


@requires_meilisearch
@pytest.mark.parametrize("model", [ProductTranslation, BlogPostTranslation])
def test_the_applied_settings_read_back_as_sent(model):
    model.update_meili_settings()
    wanted = model.get_meili_settings()

    applied = meili_client.get_index(
        model.get_meili_index_name()
    ).get_settings()

    assert applied["filterableAttributes"] == (
        wanted.filterable_attributes_payload()
    )
    assert applied["sortableAttributes"] == sorted(wanted.sortable_fields)
    assert applied["facetSearch"] is wanted.facet_search
    assert (
        applied["faceting"]["maxValuesPerFacet"]
        == (wanted.faceting["maxValuesPerFacet"])
    )
    assert applied["pagination"] == wanted.pagination
    assert applied["searchCutoffMs"] == wanted.search_cutoff_ms


@requires_meilisearch
def test_applying_the_settings_twice_changes_nothing():
    """The PreSync hook applies them on every deploy: the second run must
    leave the index as the first one did."""
    index = meili_client.get_index(ProductTranslation.get_meili_index_name())
    ProductTranslation.update_meili_settings()
    first = index.get_settings()

    ProductTranslation.update_meili_settings()

    assert index.get_settings() == first
