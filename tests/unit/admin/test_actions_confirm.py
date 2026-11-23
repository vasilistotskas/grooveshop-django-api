"""A list, row or detail action confirms before it acts, or only reads.

Unfold serves each of these actions at a URL of its own and renders its
button as a link, so the action's body must never run on a plain GET. An
action with a ``dialog`` shows its form on a GET and runs only with the
form posted back; an action that only reads is named in its admin's
``read_actions``. Every other action answers 405 to a non-POST request
(``admin.mixins.PostOnlyActionsModelAdmin``).

The walk covers every registered admin site (``all_sites``) and the actions
a class only inherits.
"""

from __future__ import annotations

import pytest
from django.contrib import admin as django_admin
from django.contrib.admin.sites import all_sites
from django.http import HttpResponseNotAllowed
from django.test import RequestFactory

from admin.platform_site import platform_admin_site

SLOTS = ("actions_list", "actions_row", "actions_detail")


def _flat(items):
    for item in items or ():
        if isinstance(item, dict):
            yield from _flat(item.get("items", ()))
        else:
            yield item


def _routed_actions():
    seen = set()
    for site in all_sites:
        for model_admin in site._registry.values():
            for slot in SLOTS:
                for name in _flat(getattr(model_admin, slot, ())):
                    key = (type(model_admin), name)
                    if key in seen:
                        continue
                    seen.add(key)
                    yield model_admin, name


ROUTED = list(_routed_actions())
IDS = [f"{type(ma).__name__}.{name}" for ma, name in ROUTED]


def test_the_walk_sees_both_sites_and_their_actions():
    assert {django_admin.site, platform_admin_site} <= set(all_sites)
    names = {f"{type(ma).__name__}.{name}" for ma, name in ROUTED}
    assert "OrderAdmin.cancel_mydata_invoice_now" in names
    assert "BoxNowShipmentAdmin.create_parcel_action" in names


@pytest.mark.parametrize(("model_admin", "name"), ROUTED, ids=IDS)
def test_an_action_confirms_or_only_reads(model_admin, name):
    method = getattr(model_admin, name)
    assert (
        getattr(method, "dialog", None) or name in model_admin.read_actions
    ), (
        f"{type(model_admin).__name__}.{name} neither confirms through a "
        "dialog nor is named in read_actions"
    )


def test_read_actions_name_real_actions():
    for site in all_sites:
        for model_admin in site._registry.values():
            routed = {
                name
                for slot in SLOTS
                for name in _flat(getattr(model_admin, slot, ()))
            }
            assert set(getattr(model_admin, "read_actions", ())) <= routed, (
                type(model_admin)
            )


def test_an_action_without_a_dialog_answers_405_to_a_get():
    from admin.base import BaseModelAdmin
    from order.models.order import Order

    class Unconfirmed(BaseModelAdmin):
        actions_detail = ["poke"]

        def poke(self, request, object_id):
            raise AssertionError("ran on a GET")

    poke = Unconfirmed(Order, django_admin.site).get_unfold_action("poke")

    response = poke.method(RequestFactory().get("/"), object_id=1)

    assert isinstance(response, HttpResponseNotAllowed)
