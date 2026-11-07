"""A click beacon never answers 500 for a broker outage.

`search_click` is open to anonymous traffic, so it is NOT routed through
`dispatch_on_commit`: every beacon in an outage would be a `DeferredDispatch`
row. It goes through `publish_or_drop`, which drops it.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from django.urls import reverse
from kombu.exceptions import OperationalError
from rest_framework import status
from rest_framework.test import APIClient

from search.tasks import save_search_click
from tenant.models import DeferredDispatch

pytestmark = pytest.mark.django_db

PAYLOAD = {
    "queryId": str(uuid.uuid4()),
    "resultId": "12",
    "resultType": "product",
    "position": 0,
}


def test_a_broker_failure_drops_the_click_and_still_accepts(caplog):
    with patch.object(
        save_search_click,
        "apply_async",
        side_effect=OperationalError("broker unreachable"),
    ):
        response = APIClient().post(
            reverse("search-click"), PAYLOAD, format="json"
        )

    assert response.status_code == status.HTTP_202_ACCEPTED
    assert not DeferredDispatch.objects.exists()
    assert any(
        f"Dropped {save_search_click.name}" in r.getMessage()
        for r in caplog.records
    )


def test_in_an_outage_the_next_click_does_not_wait_for_the_broker():
    with patch.object(
        save_search_click,
        "apply_async",
        side_effect=OperationalError("broker unreachable"),
    ) as publish:
        for _ in range(2):
            response = APIClient().post(
                reverse("search-click"), PAYLOAD, format="json"
            )
            assert response.status_code == status.HTTP_202_ACCEPTED

    publish.assert_called_once()


def test_a_bug_in_the_publish_is_not_swallowed():
    with (
        patch.object(
            save_search_click,
            "apply_async",
            side_effect=TypeError("bad arguments"),
        ),
        pytest.raises(TypeError),
    ):
        APIClient().post(reverse("search-click"), PAYLOAD, format="json")
