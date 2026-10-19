"""The price-list import reports its bad lines in the admin's language."""

from __future__ import annotations

import pytest
from django.utils import translation

from b2b.factories import CustomerGroupFactory
from b2b.services import B2BService
from product.factories.product import ProductFactory

pytestmark = pytest.mark.django_db

CSV = "SKU-OK;1.00\nSKU-OK;not-a-price\nSKU-OK;-5\nSKU-NONE;2\ngarbage"


@pytest.fixture
def group():
    ProductFactory(sku="SKU-OK", stock=1)
    return CustomerGroupFactory()


def _errors(group, language):
    with translation.override(language):
        return B2BService.import_price_lines(group, CSV)["errors"]


def test_english_errors_name_the_line(group):
    assert _errors(group, "en") == [
        "Line 2: invalid price 'not-a-price'.",
        "Line 3: negative price '-5'.",
        "Line 4: unknown SKU 'SKU-NONE'.",
        "Line 5: expected 'sku;net_price', got 'garbage'.",
    ]


def test_greek_errors_are_greek(group):
    errors = _errors(group, "el")

    assert len(errors) == 4
    assert all(error.startswith("Γραμμή ") for error in errors)
