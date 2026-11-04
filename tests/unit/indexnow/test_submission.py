"""The notification: which URLs, in one POST, and what a failure does."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
import requests
from celery.exceptions import Retry
from django.conf import settings
from django.utils import timezone

from indexnow.models import PendingUrl
from indexnow.service import (
    MAX_URLS_PER_REQUEST,
    Submission,
    queue,
    send_pending,
)
from indexnow.tasks import submit_pending_urls
from tests.unit.indexnow.conftest import STORE_HOST, setting_reader

pytestmark = pytest.mark.django_db


def answer(status_code: int) -> MagicMock:
    response = requests.Response()
    response.status_code = status_code
    return MagicMock(return_value=response)


@pytest.fixture
def post():
    with patch("indexnow.service.requests.post", answer(200)) as mock:
        yield mock


class TestTheRequest:
    def test_one_post_names_the_host_the_key_and_every_url(
        self, indexnow_on, post
    ):
        queue(["/products/1/a", "/blog/post/2/b"])

        submission = send_pending()

        post.assert_called_once()
        url = post.call_args.args[0]
        body = post.call_args.kwargs["json"]
        assert url == settings.INDEXNOW_ENDPOINT
        assert body["host"] == STORE_HOST
        assert body["key"] == indexnow_on.indexnow_key
        assert sorted(body["urlList"]) == sorted(
            [
                f"https://{STORE_HOST}/products/1/a",
                f"https://{STORE_HOST}/en/products/1/a",
                f"https://{STORE_HOST}/blog/post/2/b",
                f"https://{STORE_HOST}/en/blog/post/2/b",
            ]
        )
        assert post.call_args.kwargs["headers"] == {
            "Content-Type": "application/json; charset=utf-8"
        }
        assert post.call_args.kwargs["timeout"] == (
            settings.INDEXNOW_TIMEOUT_SECONDS
        )
        assert (submission.paths, submission.urls) == (2, 4)

    def test_a_store_that_serves_one_language_sends_one_url_per_page(
        self, indexnow_on, post
    ):
        indexnow_on.available_locales = []
        queue(["/products/1/a"])

        send_pending()

        assert post.call_args.kwargs["json"]["urlList"] == [
            f"https://{STORE_HOST}/products/1/a"
        ]

    def test_a_post_never_exceeds_the_protocols_limit(self, indexnow_on, post):
        total = MAX_URLS_PER_REQUEST // 2 + 3
        now = timezone.now() - timedelta(minutes=1)
        PendingUrl.objects.bulk_create(
            PendingUrl(path=f"/products/{n}/p", queued_at=now)
            for n in range(total)
        )

        send_pending()

        assert len(post.call_args.kwargs["json"]["urlList"]) == (
            MAX_URLS_PER_REQUEST
        )
        assert PendingUrl.objects.count() == 3

    def test_a_sent_batch_is_forgotten(self, indexnow_on, post):
        queue(["/products/1/a"])

        send_pending()

        assert PendingUrl.objects.count() == 0

    def test_an_empty_queue_sends_nothing(self, indexnow_on, post):
        assert send_pending().urls == 0

        post.assert_not_called()

    def test_a_page_that_changed_during_the_post_stays_queued(
        self, indexnow_on
    ):
        queue(["/products/1/a"])

        def requeue(*args, **kwargs):
            queue(["/products/1/a"])
            return answer(200)()

        with patch("indexnow.service.requests.post", side_effect=requeue):
            send_pending()

        assert list(PendingUrl.objects.values_list("path", flat=True)) == [
            "/products/1/a"
        ]

    def test_a_store_without_a_primary_domain_sends_nothing(
        self, indexnow_on, post
    ):
        indexnow_on.domains.all().delete()
        queue(["/products/1/a"])

        assert send_pending().urls == 0

        post.assert_not_called()
        assert PendingUrl.objects.count() == 1


class TestSwitchedOff:
    def test_a_store_that_switched_it_off_drops_its_queue(self, store, post):
        queue(["/products/1/a"])

        with patch(
            "extra_settings.models.Setting.get",
            side_effect=setting_reader(),
        ):
            send_pending()

        post.assert_not_called()
        assert PendingUrl.objects.count() == 0


class TestFailures:
    def test_a_refusal_is_dropped_and_raised(self, indexnow_on):
        queue(["/products/1/a"])

        with patch("indexnow.service.requests.post", answer(422)):
            with pytest.raises(requests.HTTPError):
                send_pending()

        assert PendingUrl.objects.count() == 0

    @pytest.mark.parametrize("status_code", [429, 503])
    def test_a_transient_failure_keeps_the_batch(
        self, indexnow_on, status_code
    ):
        queue(["/products/1/a"])

        with patch("indexnow.service.requests.post", answer(status_code)):
            with pytest.raises(requests.HTTPError):
                send_pending()

        assert PendingUrl.objects.count() == 1


def refused(status_code: int) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status_code
    return requests.HTTPError(response=response)


class TestTheTask:
    """The retry rules (``core.retry``) as the task applies them. The body
    is replaced: a task bound to a store runs in that store's schema. An
    eagerly applied task that is to be retried raises ``Retry``."""

    def test_a_4xx_is_not_retried(self):
        with patch(
            "indexnow.tasks.send_pending", side_effect=refused(403)
        ) as send:
            with pytest.raises(requests.HTTPError):
                submit_pending_urls.apply().get()

        assert send.call_count == 1

    @pytest.mark.parametrize(
        "failure",
        [refused(503), refused(429), requests.Timeout("slow")],
        ids=["5xx", "429", "timeout"],
    )
    def test_a_transient_failure_is_retried(self, failure):
        with patch("indexnow.tasks.send_pending", side_effect=failure):
            with pytest.raises(Retry):
                submit_pending_urls.apply().get()

    def test_the_result_counts_what_was_sent(self):
        with patch(
            "indexnow.tasks.send_pending",
            return_value=Submission(paths=1, urls=2),
        ):
            assert submit_pending_urls.apply().get() == {
                "paths": 1,
                "urls": 2,
            }
