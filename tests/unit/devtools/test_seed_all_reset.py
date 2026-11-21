"""``seed_all --reset-apps`` removes rows for good.

A soft-delete model's own queryset only marks its rows deleted, so a reset
through it left every order, product and builder document behind, flagged.
"""

from __future__ import annotations

import io

import pytest
from django.core.management.color import no_style

from devtools.management.commands.seed_all import ResetManager
from product.factories.product import ProductFactory
from product.models.product import Product

pytestmark = pytest.mark.django_db


def test_a_reset_removes_soft_delete_rows_for_good():
    ProductFactory(num_images=0, num_reviews=0)
    ProductFactory(num_images=0, num_reviews=0).delete()

    ResetManager(io.StringIO(), no_style()).reset_apps_data(["product"])

    assert not Product._base_manager.exists()
