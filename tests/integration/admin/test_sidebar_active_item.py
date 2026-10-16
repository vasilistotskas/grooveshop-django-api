"""The store sidebar lights up exactly the page you are on.

Left to Unfold, a sidebar entry whose page is one tab of a
``UNFOLD["TABS"]`` group was active whenever any tab of that group was
open: on the Orders page, «Παραγγελίες», «Αποδείξεις» and «Πιστωτικά
παραστατικά» were all highlighted (owner review, 2026-10-16), and the
same in Blog, Attributes, ACS and BOX NOW. ``admin.navigation`` now
decides every item's ``active`` itself.
"""

from __future__ import annotations

import pytest
from django.conf import settings
from django.test import RequestFactory
from django.urls import reverse

from admin.navigation import store_sidebar
from tests.utils.staff import stamp_platform_identity
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


def _active_links(path: str) -> list[str]:
    """The links of the active entries (titles are translated)."""
    request = RequestFactory().get(path)
    request.user = stamp_platform_identity(UserAccountFactory(admin=True))
    links = []
    for group in store_sidebar(request):
        for item in group["items"]:
            for leaf in item.get("items", [item]):
                if leaf["active"]:
                    links.append(str(leaf["link"]).split("?")[0])
    return links


def _sidebar_paths() -> set[str]:
    request = RequestFactory().get("/admin/")
    request.user = stamp_platform_identity(UserAccountFactory(admin=True))
    paths = set()
    for group in store_sidebar(request):
        for item in group["items"]:
            for leaf in item.get("items", [item]):
                paths.add(str(leaf["link"]).split("?")[0])
    return paths


TAB_PATHS = sorted(
    {
        str(tab["link"])
        for group in settings.UNFOLD["TABS"]
        for tab in group["items"]
    }
)


@pytest.mark.parametrize("path", TAB_PATHS)
def test_every_tab_page_lights_up_exactly_one_entry(path):
    assert len(_active_links(path)) == 1, _active_links(path)


ORDERS = reverse("admin:order_order_changelist")


def test_the_orders_page_lights_up_orders_only():
    assert _active_links(ORDERS) == [ORDERS]


def test_a_change_page_lights_up_its_own_entry():
    path = reverse("admin:order_invoice_change", args=[1])

    assert _active_links(path) == [reverse("admin:order_invoice_changelist")]


def test_a_tab_without_an_entry_lights_up_its_group_s_first_entry():
    """Order Items has no sidebar entry; it is a tab of the Orders group."""
    path = reverse("admin:order_orderitem_changelist")
    assert path not in _sidebar_paths()

    assert _active_links(path) == [ORDERS]


def test_the_dashboard_is_active_on_the_index_only():
    index = reverse("admin:index")

    assert _active_links(index) == [index]
    assert index not in _active_links(ORDERS)
