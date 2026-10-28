"""The table's own rules, held by the database."""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction

from texts.factories import TextOverrideFactory
from texts.models import TextOverride, TextSurface

pytestmark = pytest.mark.django_db


def test_one_row_per_surface_namespace_key_and_language():
    TextOverrideFactory(namespace="n", key="k", language="en")

    with pytest.raises(IntegrityError), transaction.atomic():
        TextOverrideFactory(namespace="n", key="k", language="en")


def test_the_same_text_may_be_overridden_in_each_language():
    TextOverrideFactory(namespace="n", key="k", language="en")
    TextOverrideFactory(namespace="n", key="k", language="el")

    assert TextOverride.objects.filter(namespace="n", key="k").count() == 2


def test_the_same_key_in_another_namespace_is_another_text():
    TextOverrideFactory(namespace="a", key="k", language="en")
    TextOverrideFactory(namespace="b", key="k", language="en")

    assert TextOverride.objects.count() == 2


def test_an_empty_value_is_refused_by_the_database():
    with pytest.raises(IntegrityError), transaction.atomic():
        TextOverrideFactory(value="")


def test_the_surface_defaults_to_the_storefront():
    row = TextOverride.objects.create(
        namespace="n", key="k", language="en", value="v"
    )

    assert row.surface == TextSurface.STOREFRONT


def test_each_change_is_kept_in_history():
    row = TextOverrideFactory(value="one")
    row.value = "two"
    row.save()

    assert [h.value for h in row.history.order_by("history_id")] == [
        "one",
        "two",
    ]
