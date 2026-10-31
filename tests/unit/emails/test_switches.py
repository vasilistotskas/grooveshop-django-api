"""The email page's data: every area has a title, and every type is listed."""

from __future__ import annotations

import pytest

from emails.registry import registry
from emails.switches import AREA_TITLES, switch_groups
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
