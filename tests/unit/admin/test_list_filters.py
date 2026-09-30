"""List filters: one kind (Unfold's, sent by "Apply") in every sheet,
and range filters that annotate only when used."""

from __future__ import annotations

import pytest
from django.contrib import admin as django_admin
from django.test import RequestFactory
from unfold.contrib.filters.admin import (
    AllValuesCheckboxFilter,
    BooleanRadioFilter,
    ChoicesDropdownFilter,
    RangeDateFilter,
    RangeDateTimeFilter,
    RelatedDropdownFilter,
)

from admin.base import BaseModelAdmin, unfold_filter_for
from admin.filters import LikesCountFilter
from admin.platform_site import platform_admin_site
from order.models.order import Order
from product.admin import ProductAdmin
from product.models.product import Product
from tenant.models import Tenant


@pytest.mark.parametrize(
    ("model", "name", "expected"),
    [
        (Order, "status", ChoicesDropdownFilter),
        (Product, "active", BooleanRadioFilter),
        (Order, "created_at", RangeDateTimeFilter),
        (Tenant, "paid_until", RangeDateFilter),
        (Order, "pay_way", RelatedDropdownFilter),
        (Order, "payment_method", AllValuesCheckboxFilter),
    ],
)
def test_a_field_name_maps_to_its_unfold_filter(model, name, expected):
    assert unfold_filter_for(model._meta.get_field(name)) is expected


def _filter_class(entry):
    if isinstance(entry, str):
        return None
    return entry[1] if isinstance(entry, tuple) else entry


def test_no_first_party_sheet_keeps_a_django_filter():
    """Every filter renders through Unfold's templates, so the whole
    sheet is one form sent by "Apply" — a Django ``SimpleListFilter``
    subclass is a link that applies on click."""
    request = RequestFactory().get("/admin/")
    django_filters = [
        f"{model._meta.label}: {entry}"
        for site in (django_admin.site, platform_admin_site)
        for model, model_admin in site._registry.items()
        if isinstance(model_admin, BaseModelAdmin)
        for entry in model_admin.get_list_filter(request)
        if (cls := _filter_class(entry)) is None
        or not cls.template.startswith("unfold/")
    ]
    assert django_filters == [], "; ".join(django_filters)


@pytest.mark.django_db
class TestAnnotatedRangeFilter:
    def _filter(self, params):
        request = RequestFactory().get("/admin/product/product/", params)
        return LikesCountFilter(
            request,
            {key: [value] for key, value in params.items()},
            Product,
            ProductAdmin(Product, django_admin.site),
        )

    def test_unused_it_adds_nothing_to_the_query(self):
        queryset = Product.objects.all()
        filtered = self._filter({}).queryset(None, queryset)
        assert "likes_count" not in filtered.query.annotations

    def test_used_it_bounds_the_annotated_value(self):
        from product.factories.favourite import ProductFavouriteFactory
        from product.factories.product import ProductFactory

        liked = ProductFactory()
        ProductFavouriteFactory(product=liked)
        unliked = ProductFactory()

        filtered = self._filter({"likes_count_from": "1"}).queryset(
            None, Product.objects.filter(pk__in=[liked.pk, unliked.pk])
        )
        assert list(filtered) == [liked]
