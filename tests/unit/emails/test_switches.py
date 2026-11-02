"""The email page's data: every area has a title, and every type is listed."""

from __future__ import annotations

import pytest

from emails.registry import registry
from emails.switches import (
    AREA_TITLES,
    PLATFORM_AUDIENCE_TITLES,
    switch_groups,
)
from emails.types import Owner

pytestmark = pytest.mark.django_db


def test_every_area_of_every_store_type_has_a_title():
    areas = {t.area for t in registry.types_for(Owner.STORE)}

    assert areas <= set(AREA_TITLES)


def test_no_title_is_for_an_empty_area():
    areas = {t.area for t in registry.types_for(Owner.STORE)}

    assert set(AREA_TITLES) <= areas


def test_every_store_type_is_listed_once():
    listed = [
        row.email_type.key for group in switch_groups() for row in group.rows
    ]

    assert sorted(listed) == sorted(
        t.key for t in registry.types_for(Owner.STORE)
    )


def test_a_platform_type_is_never_on_the_stores_page():
    platform = {t.key for t in registry.types_for(Owner.PLATFORM)}
    listed = {
        row.email_type.key for group in switch_groups() for row in group.rows
    }

    assert platform
    assert platform.isdisjoint(listed)


def test_every_audience_of_a_platform_type_has_a_title():
    audiences = {t.audience for t in registry.types_for(Owner.PLATFORM)}

    assert audiences <= set(PLATFORM_AUDIENCE_TITLES)


def test_the_platform_page_lists_every_platform_type_once_by_audience():
    groups = switch_groups(owner=Owner.PLATFORM)

    listed = [row.email_type.key for group in groups for row in group.rows]
    assert sorted(listed) == sorted(
        t.key for t in registry.types_for(Owner.PLATFORM)
    )
    for group in groups:
        assert {row.email_type.audience.value for row in group.rows} == {
            group.area
        }


def test_the_platform_page_lists_no_store_type():
    store = {t.key for t in registry.types_for(Owner.STORE)}
    listed = {
        row.email_type.key
        for group in switch_groups(owner=Owner.PLATFORM)
        for row in group.rows
    }

    assert store.isdisjoint(listed)


def test_every_platform_type_is_required_so_nothing_is_switchable():
    assert not any(
        row.switchable
        for group in switch_groups(owner=Owner.PLATFORM)
        for row in group.rows
    )
