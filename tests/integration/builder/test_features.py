"""``GET builder/features``: which store features are on, and where this
person switches one on, for the editor's "Turn on <feature>"."""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest
from django.urls import reverse
from extra_settings.models import Setting
from rest_framework import status
from rest_framework.test import APIClient

from builder.serializers import StoreFeatureStatesSerializer
from core.utils.tenant_urls import get_tenant_api_base_url
from tenant.features import STORE_FEATURES, feature_plan_flags
from tests.integration.builder.conftest import api_as
from tests.utils.store_settings import (
    switch_every_feature_on,
    switch_store_settings,
)

pytestmark = pytest.mark.django_db


def get(user):
    return api_as(user).get(reverse("builder:features"))


def every_feature_on(tenant) -> None:
    """The plan and every setting of every feature."""
    for flag in feature_plan_flags():
        setattr(tenant, flag, True)
    switch_every_feature_on()


def camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part.title() for part in rest)


def change_page_of(name: str) -> str:
    """The path of the admin page that edits setting *name*."""
    return reverse(
        "admin:extra_settings_setting_change",
        args=[Setting.objects.get(name=name).pk],
    )


def test_the_answer_names_every_feature_of_the_table():
    assert set(StoreFeatureStatesSerializer().fields) == set(STORE_FEATURES)


class TestAnAdmin:
    def test_every_feature_is_named_in_the_wire_case(self, admin, tenant):
        every_feature_on(tenant)

        body = get(admin).json()

        assert set(body["features"]) == {camel(n) for n in STORE_FEATURES}
        assert all(f["on"] for f in body["features"].values())
        assert all(f["turnOnUrl"] is None for f in body["features"].values())

    def test_a_feature_with_its_setting_off_says_where_to_turn_it_on(
        self, admin, tenant
    ):
        every_feature_on(tenant)
        switch_store_settings(LOYALTY_ENABLED=False)

        response = get(admin)

        loyalty = response.json()["features"]["loyalty"]
        assert response.status_code == status.HTTP_200_OK
        assert loyalty["on"] is False
        url = urlsplit(loyalty["turnOnUrl"])
        assert url.path == change_page_of("LOYALTY_ENABLED")
        assert get(admin).json()["features"]["blog"]["turnOnUrl"] is None

    def test_the_first_setting_that_is_off_is_the_one_offered(
        self, admin, tenant
    ):
        every_feature_on(tenant)
        switch_store_settings(CATALOGUE_ENABLED=False, CART_ENABLED=False)

        features = get(admin).json()["features"]

        # The cart needs the catalogue first.
        catalogue = urlsplit(features["catalogue"]["turnOnUrl"]).path
        assert catalogue == change_page_of("CATALOGUE_ENABLED")
        assert urlsplit(features["cart"]["turnOnUrl"]).path == catalogue

    def test_a_feature_the_plan_keeps_off_has_no_link(self, admin, tenant):
        every_feature_on(tenant)
        tenant.loyalty_enabled = False

        loyalty = get(admin).json()["features"]["loyalty"]

        assert loyalty == {"on": False, "turnOnUrl": None}

    def test_a_setting_with_no_row_has_no_page_to_link_to(self, admin, tenant):
        every_feature_on(tenant)
        Setting.objects.filter(name="NEWSLETTER_ENABLED").delete()

        newsletter = get(admin).json()["features"]["newsletter"]

        assert newsletter["turnOnUrl"] is None

    def test_the_link_is_for_the_stores_own_admin_host(self, admin, tenant):
        every_feature_on(tenant)
        switch_store_settings(NEWSLETTER_ENABLED=False)

        url = get(admin).json()["features"]["newsletter"]["turnOnUrl"]

        assert url.startswith(get_tenant_api_base_url() + "/")
        assert url.endswith(change_page_of("NEWSLETTER_ENABLED"))


class TestOtherRoles:
    def test_staff_who_only_look_see_the_state_but_no_link(self, staff, tenant):
        every_feature_on(tenant)
        switch_store_settings(LOYALTY_ENABLED=False)

        loyalty = get(staff).json()["features"]["loyalty"]

        assert loyalty == {"on": False, "turnOnUrl": None}

    def test_a_shopper_and_a_visitor_may_not(self, tenant):
        from user.factories.account import UserAccountFactory

        for who in (api_as(UserAccountFactory()), APIClient()):
            response = who.get(reverse("builder:features"))

            assert response.status_code in (
                status.HTTP_401_UNAUTHORIZED,
                status.HTTP_403_FORBIDDEN,
            )


def test_it_costs_a_fixed_number_of_queries(
    admin, tenant, django_assert_max_num_queries
):
    every_feature_on(tenant)
    get(admin)

    with django_assert_max_num_queries(8):
        response = get(admin)

    assert response.status_code == status.HTTP_200_OK
