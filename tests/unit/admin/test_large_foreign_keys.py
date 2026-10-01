"""No admin form renders a plain select over a large table.

A ``<select>`` holds one ``<option>`` per row: an order-item form
listing every order, or a review form listing every customer, loads the
whole table on each open and grows with the store. A foreign key to one
of these models is an autocomplete, or read-only.
"""

from __future__ import annotations

import pytest
from django.contrib import admin as django_admin
from django.contrib.admin.options import InlineModelAdmin
from django.test import RequestFactory

from admin.base import BaseModelAdmin
from admin.platform_site import platform_admin_site

LARGE_MODELS = frozenset(
    {
        "user.UserAccount",
        "product.Product",
        "order.Order",
        "order.OrderItem",
        "cart.Cart",
        "blog.BlogPost",
        "giftcard.GiftCard",
    }
)


def _editable_selects(model_admin, request):
    """``(field, target)`` for each large-table relation rendered as a
    plain select by this admin's form. A read-only admin (no add, no
    change) renders none."""
    if isinstance(model_admin, InlineModelAdmin):
        writable = model_admin.has_add_permission(
            request, None
        ) or model_admin.has_change_permission(request)
    else:
        writable = model_admin.has_add_permission(
            request
        ) or model_admin.has_change_permission(request)
    if not writable:
        return
    readonly = set(model_admin.get_readonly_fields(request))
    widgets = set(model_admin.get_autocomplete_fields(request))
    if isinstance(model_admin, InlineModelAdmin):
        form_fields = model_admin.get_formset(request).form.base_fields
    else:
        form_fields = model_admin.get_form(request).base_fields
    for field in model_admin.model._meta.get_fields():
        if not (field.is_relation and field.concrete and field.editable):
            continue
        if field.related_model is None:
            continue
        target = field.related_model._meta.label
        if target not in LARGE_MODELS:
            continue
        if field.name in readonly or field.name in widgets:
            continue
        if field.name not in form_fields:
            continue
        yield field.name, target


def _admins():
    for site in (django_admin.site, platform_admin_site):
        for model_admin in site._registry.values():
            if not isinstance(model_admin, BaseModelAdmin):
                continue
            yield model_admin
            yield from (
                inline(model_admin.model, site)
                for inline in model_admin.inlines
            )


@pytest.mark.django_db
def test_no_form_lists_a_large_table():
    request = RequestFactory().get("/admin/")
    from user.models import UserAccount

    request.user = UserAccount(is_staff=True, is_superuser=True)
    offending = sorted(
        {
            f"{type(model_admin).__name__}.{name} -> {target}"
            for model_admin in _admins()
            for name, target in _editable_selects(model_admin, request)
        }
    )
    assert offending == [], "; ".join(offending)
