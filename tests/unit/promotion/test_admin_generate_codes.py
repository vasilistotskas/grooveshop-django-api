"""Bulk code generation: exact count, collisions replaced, not a scan
of every code in the table."""

from __future__ import annotations

from itertools import chain, repeat
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from promotion.admin import _create_codes
from promotion.factories.promotion import (
    PromotionCodeFactory,
    PromotionFactory,
)
from promotion.models import PromotionCode

pytestmark = pytest.mark.django_db


def test_creates_the_requested_number_with_the_prefix():
    promotion = PromotionFactory()

    created = _create_codes(
        promotion, 25, prefix="VIP-", length=8, usage_limit=1
    )

    codes = PromotionCode.objects.filter(promotion=promotion)
    assert created == 25
    assert codes.count() == 25
    assert all(code.code.startswith("VIP-") for code in codes)
    assert all(code.usage_limit == 1 for code in codes)


def test_a_taken_code_is_replaced():
    PromotionCodeFactory(code="TAKEN")
    promotion = PromotionFactory()
    values = chain(["TAKEN", "FRESH1"], repeat("FRESH2"))

    with patch(
        "promotion.admin._generate_code", side_effect=lambda *_: next(values)
    ):
        created = _create_codes(
            promotion, 2, prefix="", length=6, usage_limit=None
        )

    assert created == 2
    assert set(
        PromotionCode.objects.filter(promotion=promotion).values_list(
            "code", flat=True
        )
    ) == {"FRESH1", "FRESH2"}


def test_the_existing_codes_are_not_loaded():
    PromotionCodeFactory.create_batch(30)
    promotion = PromotionFactory()

    with CaptureQueriesContext(connection) as queries:
        _create_codes(promotion, 5, prefix="", length=10, usage_limit=None)

    for query in queries.captured_queries:
        if 'FROM "promotion_code"' in query["sql"]:
            assert " IN (" in query["sql"], query["sql"]
