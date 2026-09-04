"""Click tracking: endpoint validation, attribution, and the CTR task.

Scenario data mirrors real production queries (``bataria kinitou``,
``optiki ina``, per-keystroke fragments) pulled from the live
SearchQuery table during Phase-1 revalidation.
"""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from search.models import SearchClick, SearchQuery
from search.tasks import (
    _SearchQueryNotSavedYet,
    save_search_click,
    update_click_scores,
)


@pytest.fixture
def api_client():
    return APIClient()


def _make_query(query="bataria kinitou", **overrides) -> SearchQuery:
    defaults = {
        "uuid": uuid4(),
        "query": query,
        "language_code": "el",
        "content_type": "federated",
        "results_count": 5,
        "estimated_total_hits": 5,
    }
    defaults.update(overrides)
    return SearchQuery.objects.create(**defaults)


@pytest.mark.django_db
class TestSearchClickEndpoint:
    url = "/api/v1/search/click"

    def test_valid_click_is_persisted(self, api_client):
        search_query = _make_query()
        response = api_client.post(
            self.url,
            {
                "query_id": str(search_query.uuid),
                "result_id": "2",
                "result_type": "product",
                "position": 0,
            },
            format="json",
        )
        assert response.status_code == 202
        click = SearchClick.objects.get()
        assert click.search_query == search_query
        assert click.result_id == "2"
        assert click.result_type == "product"
        assert click.position == 0

    def test_blog_post_click(self, api_client):
        search_query = _make_query(query="optiki ina")
        response = api_client.post(
            self.url,
            {
                "query_id": str(search_query.uuid),
                "result_id": "78",
                "result_type": "blog_post",
                "position": 1,
            },
            format="json",
        )
        assert response.status_code == 202
        assert SearchClick.objects.get().result_type == "blog_post"

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {
                "query_id": "not-a-uuid",
                "result_id": "1",
                "result_type": "product",
                "position": 0,
            },
            {
                "query_id": str(uuid4()),
                "result_id": "1",
                "result_type": "invalid",
                "position": 0,
            },
            {
                "query_id": str(uuid4()),
                "result_id": "1",
                "result_type": "product",
                "position": -1,
            },
        ],
    )
    def test_invalid_payload_is_rejected(self, api_client, payload):
        response = api_client.post(self.url, payload, format="json")
        assert response.status_code == 400
        assert SearchClick.objects.count() == 0

    def test_anonymous_access_is_allowed(self, api_client):
        # Guest visitors click search results too — the endpoint must
        # not require authentication.
        search_query = _make_query()
        response = api_client.post(
            self.url,
            {
                "query_id": str(search_query.uuid),
                "result_id": "5",
                "result_type": "product",
                "position": 3,
            },
            format="json",
        )
        assert response.status_code == 202


@pytest.mark.django_db
class TestSaveSearchClickTask:
    def test_unknown_query_uuid_signals_retry(self):
        # save_search_query runs async, so a click can race the row —
        # the task must raise the retryable signal, not swallow it.
        with pytest.raises(_SearchQueryNotSavedYet):
            save_search_click.run(
                query_uuid=str(uuid4()),
                result_id="1",
                result_type="product",
                position=0,
            )


@pytest.mark.django_db
class TestUpdateClickScores:
    def _click(self, search_query, result_id, result_type, days_ago=0, n=1):
        for _ in range(n):
            click = SearchClick.objects.create(
                search_query=search_query,
                result_id=str(result_id),
                result_type=result_type,
                position=0,
            )
            if days_ago:
                SearchClick.objects.filter(pk=click.pk).update(
                    timestamp=timezone.now() - timedelta(days=days_ago)
                )

    def test_scores_reflect_recent_clicks_only(self):
        from blog.factories.post import BlogPostFactory
        from product.factories.product import ProductFactory

        product_hot = ProductFactory()
        product_cold = ProductFactory()
        post_hot = BlogPostFactory()

        search_query = _make_query()
        # Real scenario: the power bank gets repeat clicks, another
        # product a single (noise) click, an article steady clicks, and
        # stale clicks outside the window must not count.
        self._click(search_query, product_hot.pk, "product", n=5)
        self._click(search_query, product_cold.pk, "product", n=1)
        self._click(search_query, post_hot.pk, "blog_post", n=3)
        self._click(search_query, product_hot.pk, "product", days_ago=45, n=7)

        update_click_scores.run()

        product_hot.refresh_from_db()
        product_cold.refresh_from_db()
        post_hot.refresh_from_db()
        assert product_hot.click_score == 5
        assert product_cold.click_score == 0  # single click = noise
        assert post_hot.click_score == 3

    def test_items_out_of_window_are_reset(self):
        from product.factories.product import ProductFactory

        product = ProductFactory()
        product.click_score = 9
        product.save(update_fields=["click_score"])

        update_click_scores.run()

        product.refresh_from_db()
        assert product.click_score == 0

    def test_top_list_is_capped(self, monkeypatch):
        import search.tasks as tasks_module
        from product.factories.product import ProductFactory

        monkeypatch.setattr(tasks_module, "CLICK_SCORE_MAX_ITEMS", 2)
        search_query = _make_query()
        products = [ProductFactory() for _ in range(3)]
        for clicks, product in zip((6, 4, 2), products, strict=True):
            self._click(search_query, product.pk, "product", n=clicks)

        update_click_scores.run()

        for product in products:
            product.refresh_from_db()
        assert products[0].click_score == 6
        assert products[1].click_score == 4
        assert products[2].click_score == 0  # beyond the cap

    def test_no_clicks_is_a_safe_noop(self):
        result = update_click_scores.run()
        assert result == {"product": 0, "blog_post": 0}


@pytest.mark.django_db
class TestSaveSearchQueryTask:
    def test_oversized_query_is_truncated_not_lost(self):
        """A pasted >500-char query must not kill the task at the DB
        layer (CharField max_length is only enforced by Postgres on
        .create()) — the row is stored truncated instead."""
        from search.tasks import save_search_query

        save_search_query.run(
            query="x" * 600,
            language_code="el",
            content_type="federated",
            results_count=0,
            estimated_total_hits=0,
            processing_time_ms=5,
            user_id=None,
            session_key=None,
            ip_address=None,
            user_agent="pytest",
            query_uuid=str(uuid4()),
        )
        row = SearchQuery.objects.get()
        assert len(row.query) == 500
