"""The admin's ⌘K palette (``admin.search``).

First the site's own sidebar pages, matched without case or accents,
then the other screens the viewer can open by their own name, then a
few records of each - never a screen of a feature the store does not
have.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib import admin
from django.test import RequestFactory
from django.urls import reverse
from django.utils import translation

from admin.search import RECORDS_PER_MODEL, Screen, fold, page_results
from order.factories.order import OrderFactory
from tests.utils.staff import (
    bind_store_tenant,
    stamp_platform_identity,
    store_tenant,
    unbind_store_tenant,
)
from user.factories.account import UserAccountFactory

# The shape ``UnfoldAdminSite.get_sidebar_list`` returns.
SIDEBAR: list[dict[str, Any]] = [
    {
        "title": None,
        "items": [
            {"title": "Dashboard", "link": "/admin/", "has_permission": True}
        ],
    },
    {
        "title": "Sales",
        "items": [
            {
                "title": "Παραγγελίες",
                "link": "/admin/order/order/",
                "icon": "receipt_long",
                "has_permission": True,
            },
            {
                "title": "Receipts",
                "link": "/admin/order/invoice/",
                "has_permission": True,
            },
        ],
    },
    {
        "title": "Customers",
        "items": [
            {
                "title": "Customers",
                "link": "/admin/user/useraccount/",
                "has_permission": True,
            },
            {
                "title": "Wholesale",
                "icon": "storefront",
                "link": "/admin/b2b/businessprofile/",
                "has_permission": True,
                "items": [
                    {
                        "title": "Business Customers",
                        "link": "/admin/b2b/businessprofile/",
                        "has_permission": True,
                    },
                    {
                        "title": "Customer Groups",
                        "link": "/admin/b2b/customergroup/",
                        "has_permission": False,
                    },
                ],
            },
        ],
    },
]

# Screens from Django's app list: one the sidebar lists, two it does not.
SCREENS = [
    Screen(None, "Orders", "Orders", "/admin/order/order/"),
    Screen(None, "Order history", "Orders", "/admin/order/historicalorder/"),
    Screen(None, "Cart items", "Carts", "/admin/cart/cartitem/"),
]


def _titles(term: str) -> list[str]:
    return [result.title for result in page_results(SIDEBAR, SCREENS, term)]


class TestPages:
    def test_case_and_accents_do_not_matter(self):
        assert fold("ΠΑΡΑΓΓΕΛΊΕΣ") == fold("παραγγελιες")
        assert _titles("παραγγελιες") == ["Παραγγελίες"]

    def test_the_closest_title_comes_first(self):
        # Exact title, then a word starting with the term. Customer
        # Groups is not shown: the viewer may not open it.
        assert _titles("customers") == ["Customers", "Business Customers"]

    def test_a_section_name_lists_its_pages(self):
        assert _titles("sales") == ["Παραγγελίες", "Receipts"]

    def test_a_sub_menu_page_says_where_it_is(self):
        (result,) = page_results(SIDEBAR, [], "business")
        assert result.description == "Customers › Wholesale"
        assert result.icon == "storefront"

    def test_a_sub_menu_name_lists_its_pages(self):
        # Customer Groups stays out: the viewer may not open it.
        assert _titles("wholesale") == ["Business Customers"]

    def test_a_page_named_like_its_sub_menu_is_not_described_twice(self):
        sidebar = [
            {
                "title": "Marketing",
                "items": [
                    {
                        "title": "Gift Cards",
                        "link": "/admin/giftcard/giftcard/",
                        "has_permission": True,
                        "items": [
                            {
                                "title": "Gift Cards",
                                "link": "/admin/giftcard/giftcard/",
                                "has_permission": True,
                            },
                            {
                                "title": "Transactions",
                                "link": "/admin/giftcard/transaction/",
                                "has_permission": True,
                            },
                        ],
                    }
                ],
            }
        ]

        results = page_results(sidebar, [], "gift")

        assert [(r.title, r.description) for r in results] == [
            ("Gift Cards", "Marketing"),
            ("Transactions", "Marketing › Gift Cards"),
        ]

    def test_a_page_outside_any_section_is_described_as_home(self):
        with translation.override("en"):
            (result,) = page_results(SIDEBAR, [], "dash")
        assert result.description == "Home"


class TestOtherScreens:
    def test_a_screen_the_sidebar_leaves_out_is_found_by_its_name(self):
        (result,) = page_results(SIDEBAR, SCREENS, "cart")
        assert (result.title, result.description) == ("Cart items", "Carts")

    def test_sidebar_pages_come_before_other_screens(self):
        titles = _titles("c")
        assert titles[0] == "Customers"
        assert titles[-1] == "Cart items"

    def test_an_app_name_alone_does_not_list_its_screens(self):
        # "Carts" is the app of Cart items, not its name.
        assert _titles("carts") == []

    def test_a_page_the_sidebar_lists_is_not_offered_again(self):
        # The sidebar lists the orders changelist (as "Παραγγελίες"), so
        # the screen named "Orders" at that address is not repeated.
        assert _titles("orders") == []


@pytest.mark.django_db
class TestStoreSearch:
    @pytest.fixture(autouse=True)
    def _english(self):
        with translation.override("en"):
            yield

    @pytest.fixture
    def bound(self):
        bound = []

        def bind(**plans: bool):
            bound.append(bind_store_tenant(store_tenant("palette", **plans)))

        yield bind
        for previous in bound:
            unbind_store_tenant(previous)

    def _results(self, term: str) -> list[Any]:
        request = RequestFactory().get("/admin/search/", {"s": term})
        request.user = stamp_platform_identity(UserAccountFactory(admin=True))
        response = admin.site.search(request)
        return list(response.context_data["results"])

    def test_a_page_is_found_without_its_accents(self, bound):
        bound()
        with translation.override("el"):
            results = self._results("παραγγελιες")
            orders = reverse("admin:order_order_changelist")

        assert results[0].link == orders

    def test_a_screen_off_the_sidebar_is_reachable(self, bound):
        bound()
        links = [result.link for result in self._results("cart items")]

        assert reverse("admin:cart_cartitem_changelist") in links

    def test_a_feature_the_store_does_not_have_is_not_offered(self, bound):
        bound(gift_cards_enabled=False)
        links = [result.link for result in self._results("gift")]

        assert not [link for link in links if "/giftcard/" in link]

    def test_a_feature_the_store_has_is_offered(self, bound):
        bound(gift_cards_enabled=True)
        links = [result.link for result in self._results("gift")]

        assert [link for link in links if "/giftcard/" in link]

    def test_records_are_capped_with_a_link_to_the_rest(self, bound):
        bound()
        OrderFactory.create_batch(
            RECORDS_PER_MODEL + 2,
            email="palette-buyer@example.com",
            num_order_items=0,
        )

        results = self._results("palette-buyer")

        changelist = reverse("admin:order_order_changelist")
        orders = [r for r in results if r.link.startswith(changelist)]
        changes = [r for r in orders if r.link.endswith("/change/")]
        assert len(changes) == RECORDS_PER_MODEL
        assert orders[-1].link == f"{changelist}?q=palette-buyer"
        # Listed under the sidebar's own icon for orders.
        assert {r.icon for r in changes} == {"receipt_long"}

    def test_the_sidebars_records_come_first(self, bound):
        bound()
        OrderFactory(email="first-records@example.com", num_order_items=0)

        results = self._results("first-records")
        records = [r for r in results if r.link.endswith("/change/")]

        assert records[0].link.startswith(
            reverse("admin:order_order_changelist")
        )

    def test_one_letter_matches_pages_only(self, bound):
        bound()
        OrderFactory(email="a@example.com", num_order_items=0)

        results = self._results("a")

        assert results
        assert not [r for r in results if r.link.endswith("/change/")]

    def test_an_empty_term_answers_nothing(self, bound):
        bound()
        request = RequestFactory().get("/admin/search/", {"s": "  "})
        request.user = stamp_platform_identity(UserAccountFactory(admin=True))

        response = admin.site.search(request)

        assert response.status_code == 200
        assert response.content == b""
