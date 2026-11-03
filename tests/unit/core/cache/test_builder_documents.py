"""The storefront is told which page-builder documents to purge."""

from __future__ import annotations

from unittest import mock

import pytest

from builder import data_purge
from core.cache import nuxt as nuxt_client
from core.cache.service import CacheService

pytestmark = pytest.mark.django_db


@pytest.fixture
def _configured(settings):
    settings.NUXT_INTERNAL_BASE_URL = "http://frontend-nuxt-service:80"
    settings.NUXT_CACHE_PURGE_TOKEN = "secret-token"


@pytest.fixture(autouse=True)
def _host(monkeypatch):
    monkeypatch.setattr(
        nuxt_client, "_current_tenant_host", lambda: "s.example"
    )


class TestTheRequest:
    def test_documents_alone_are_sent(self, _configured):
        with mock.patch("core.cache.nuxt.requests.post") as post:
            post.return_value.json.return_value = {
                "matched": 2,
                "deleted": 2,
                "blocked": 0,
            }

            result = nuxt_client.request_purge(
                [], builder_documents=["template:home", "page:a"]
            )

        payload = post.call_args.kwargs["json"]
        assert payload["patterns"] == []
        assert payload["builderDocuments"] == ["template:home", "page:a"]
        assert payload["host"] == "s.example"
        assert result.deleted == 2

    def test_nothing_to_send_sends_nothing(self, _configured):
        with mock.patch("core.cache.nuxt.requests.post") as post:
            nuxt_client.request_purge([])

        post.assert_not_called()


class TestTheSurface:
    def test_the_queued_documents_go_in_batches_of_64(self, _configured):
        documents = [f"page:p{i:03d}" for i in range(70)]
        with (
            mock.patch.object(data_purge, "take", return_value=documents),
            mock.patch("core.cache.nuxt.requests.post") as post,
        ):
            post.return_value.json.return_value = {
                "matched": 1,
                "deleted": 1,
                "blocked": 0,
            }

            report = CacheService.purge(["builder_documents"])

        sent = [
            call.kwargs["json"]["builderDocuments"]
            for call in post.call_args_list
        ]
        assert [len(batch) for batch in sent] == [64, 6]
        assert [d for batch in sent for d in batch] == documents
        assert report.total_nuxt == 2

    def test_a_dry_run_takes_nothing(self, _configured):
        with (
            mock.patch.object(data_purge, "take") as take,
            mock.patch("core.cache.nuxt.requests.post") as post,
        ):
            CacheService.purge(["builder_documents"], dry_run=True)

        take.assert_not_called()
        post.assert_not_called()
