"""A feature gate's 404 is logged, so it can be told from a missing object.

The response stays a bare 404 (the route must look unregistered). The log
line is what the ``GrooveShopFeatureGateRefused`` alert counts: a client
still calling a route its store has switched off.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from django.db import connection
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from tenant.permissions import REFUSED_MESSAGE
from tests.utils.staff import store_tenant

pytestmark = pytest.mark.django_db


def _refusals(caplog):
    return [
        record
        for record in caplog.records
        if record.name == "tenant.permissions"
        and record.getMessage() == REFUSED_MESSAGE
    ]


def test_a_plan_gate_refusal_is_logged(caplog, monkeypatch):
    tenant = store_tenant("gate_log_blog_off", blog_enabled=False)
    monkeypatch.setattr(connection, "tenant", tenant, raising=False)

    with caplog.at_level(logging.INFO, logger="tenant.permissions"):
        response = APIClient().get(reverse("blog-post-list"))

    assert response.status_code == status.HTTP_404_NOT_FOUND
    (record,) = _refusals(caplog)
    assert record.fields == {
        "gate": "IsBlogEnabled",
        "switch": "blog_enabled",
        "route": "api/v1/blog/post",
        "method": "GET",
    }


def test_a_store_setting_refusal_is_logged(caplog):
    with (
        patch(
            "extra_settings.models.Setting.get",
            side_effect=lambda key, default=None: (
                False if key == "PRODUCT_REVIEWS_ENABLED" else default
            ),
        ),
        caplog.at_level(logging.INFO, logger="tenant.permissions"),
    ):
        response = APIClient().get(reverse("product-review-list"))

    assert response.status_code == status.HTTP_404_NOT_FOUND
    (record,) = _refusals(caplog)
    assert record.fields["gate"] == "IsProductReviewsEnabled"
    assert record.fields["switch"] == "PRODUCT_REVIEWS_ENABLED"


def test_an_open_gate_logs_nothing(caplog, monkeypatch):
    tenant = store_tenant("gate_log_blog_on", blog_enabled=True)
    monkeypatch.setattr(connection, "tenant", tenant, raising=False)

    with caplog.at_level(logging.INFO, logger="tenant.permissions"):
        response = APIClient().get(reverse("blog-post-list"))

    assert response.status_code == status.HTTP_200_OK
    assert _refusals(caplog) == []
