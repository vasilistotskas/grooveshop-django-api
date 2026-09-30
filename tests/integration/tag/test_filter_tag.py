"""``TagFilter`` (tag/filters/tag.py) through ``/api/v1/tag``.

Every case asserts the exact set of tags returned from one fixed
dataset, so an undeclared parameter — which django-filter ignores
silently — fails instead of passing on the unfiltered list.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APIClient

from product.factories.product import ProductFactory
from tag.factories.tagged_item import TaggedProductFactory
from tag.models import Tag

pytestmark = pytest.mark.django_db

URL = reverse("tag-list")


def _tag(label, *, active=True, sort_order):
    # One Greek label only: the factory's random labels in the other
    # languages could match a substring filter by chance.
    tag = Tag.objects.create(active=active, sort_order=sort_order)
    tag.set_current_language("el")
    tag.label = label
    tag.save()
    return tag


@pytest.fixture
def tags():
    """python: on 2 products. django: on 1. archive: unused.
    hidden: inactive, on 1."""
    python = _tag("Python", sort_order=1)
    django = _tag("Django", sort_order=2)
    archive = _tag("Archive", sort_order=3)
    hidden = _tag("Hidden", active=False, sort_order=4)

    first, second = ProductFactory.create_batch(2, num_images=0, num_reviews=0)
    for tag, product in (
        (python, first),
        (python, second),
        (django, first),
        (hidden, second),
    ):
        TaggedProductFactory(tag=tag, content_object=product)

    return {"python": python, "archive": archive, "product": first}


def _labels(params) -> set[str]:
    response = APIClient().get(URL, params)
    assert response.status_code == 200, response.data
    return {
        tag["translations"]["el"]["label"] for tag in response.data["results"]
    }


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"active": "true"}, {"Python", "Django", "Archive"}),
        ({"active": "false"}, {"Hidden"}),
        ({"label": "yth"}, {"Python"}),
        ({"label__startswith": "dj"}, {"Django"}),
        ({"label__exact": "Django"}, {"Django"}),
        ({"has_usage": "true"}, {"Python", "Django", "Hidden"}),
        ({"has_usage": "false"}, {"Archive"}),
        ({"unused": "true"}, {"Archive"}),
        ({"min_usage_count": 2}, {"Python"}),
        ({"max_usage_count": 0}, {"Archive"}),
        ({"content_type": "product"}, {"Python", "Django", "Hidden"}),
        ({"content_type__app_label": "blog"}, set()),
        # The camelCase spelling the storefront sends reaches the same
        # filters.
        ({"hasUsage": "false"}, {"Archive"}),
        ({"minUsageCount": 2}, {"Python"}),
        ({"active": "true", "has_usage": "true"}, {"Python", "Django"}),
    ],
)
def test_each_filter_selects_exactly_its_tags(tags, params, expected):
    assert _labels(params) == expected


def test_object_id_selects_the_tags_on_that_object(tags):
    assert _labels({"object_id": tags["product"].id}) == {"Python", "Django"}


def test_uuid_selects_one_tag(tags):
    assert _labels({"uuid": str(tags["archive"].uuid)}) == {"Archive"}


def test_ranking_by_usage(tags):
    response = APIClient().get(URL, {"ordering": "-usageCount"})

    labels = [
        tag["translations"]["el"]["label"] for tag in response.data["results"]
    ]
    assert labels[0] == "Python"
    assert labels[-1] == "Archive"


def test_has_label_splits_labelled_from_unlabelled_tags(tags):
    """A blank label and no translation at all both count as unlabelled;
    a tag is labelled only when none of its translations is blank."""
    blank = _tag("", sort_order=5)
    bare = Tag.objects.create(active=True, sort_order=6)

    def ids(params):
        response = APIClient().get(URL, params)
        assert response.status_code == 200, response.data
        return sorted(tag["id"] for tag in response.data["results"])

    assert ids({"has_label": "false"}) == sorted([blank.id, bare.id])
    assert _labels({"hasLabel": "true"}) == {
        "Python",
        "Django",
        "Archive",
        "Hidden",
    }


def test_the_listed_usage_counts(tags):
    response = APIClient().get(URL, {"ordering": "sortOrder"})

    assert response.status_code == 200, response.data
    counts = {
        tag["translations"]["el"]["label"]: tag["usage_count"]
        for tag in response.data["results"]
    }
    assert counts == {
        "Python": "2",
        "Django": "1",
        "Archive": "0",
        "Hidden": "1",
    }


def test_listing_costs_the_same_queries_for_one_tag_or_many(
    tags, django_assert_num_queries
):
    """The counts come from the list annotation, not a query per tag."""
    client = APIClient()
    with CaptureQueriesContext(connection) as three_tags:
        client.get(URL)
    for sort_order in range(10, 20):
        TaggedProductFactory(
            tag=_tag(f"Extra {sort_order}", sort_order=sort_order),
            content_object=tags["product"],
        )

    with django_assert_num_queries(len(three_tags)):
        client.get(URL)
