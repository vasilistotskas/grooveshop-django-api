"""``builder.storefront``: the catalogue, asked once and revalidated."""

from __future__ import annotations

from unittest import mock

import pytest
import requests

from builder import storefront
from core.storefront import StorefrontUnavailable
from tests.utils.builder import HASH, default_blocks


@pytest.fixture(autouse=True)
def _clean():
    storefront.clear_catalogue_cache()
    yield
    storefront.clear_catalogue_cache()


def test_the_catalogue_is_parsed_per_block(catalogue):
    result = storefront.fetch_catalogue("shop")

    assert result.hash == HASH
    hero = result.get("hero")
    assert hero is not None
    assert (hero.version, hero.versioned_type) == (2, "hero@2")
    assert hero.accepts == {1}
    assert hero.max_blocks == 3
    assert hero.children_allowed == {"slide"}
    assert hero.children_max_depth == 1
    assert hero.templates == {"home"}
    assert hero.groups is None
    assert hero.refs["/productId"] == "product"
    assert hero.rich_text == ("/body",)
    newsletter = result.get("newsletter")
    assert newsletter is not None
    assert newsletter.feature == "newsletter"
    assert result.get("nothing") is None


def test_it_asks_for_the_store_by_schema(catalogue):
    storefront.fetch_catalogue("shop")

    (call,) = catalogue.calls
    assert call["url"] == "http://storefront.test/api/builder/catalogue"
    assert call["params"] == {"schema": "shop"}


def test_a_process_keeps_its_copy_and_revalidates_by_etag(catalogue, settings):
    settings.BUILDER_CATALOGUE_REVALIDATE_SECONDS = 0
    first = storefront.fetch_catalogue("shop")

    again = storefront.fetch_catalogue("shop")

    assert again is first
    assert catalogue.calls[1]["headers"]["If-None-Match"] == f'"{HASH}"'


def test_inside_the_window_it_does_not_ask_again(catalogue):
    storefront.fetch_catalogue("shop")
    storefront.fetch_catalogue("shop")

    assert len(catalogue.calls) == 1


def test_a_changed_build_replaces_the_copy(catalogue, settings):
    settings.BUILDER_CATALOGUE_REVALIDATE_SECONDS = 0
    storefront.fetch_catalogue("shop")
    catalogue.hash = "next-build"
    catalogue.blocks = default_blocks()[:1]

    result = storefront.fetch_catalogue("shop")

    assert result.hash == "next-build"
    assert result.get("slide") is None


@pytest.mark.parametrize(
    "failure",
    [requests.ConnectTimeout("slow"), requests.ConnectionError("down")],
)
def test_with_no_copy_a_failure_refuses(catalogue, failure):
    catalogue.failure = failure

    with pytest.raises(StorefrontUnavailable):
        storefront.fetch_catalogue("shop")


def test_with_no_copy_a_bad_status_or_a_bad_shape_refuses(catalogue):
    catalogue.status = 500
    with pytest.raises(StorefrontUnavailable):
        storefront.fetch_catalogue("shop")

    catalogue.status = 200
    catalogue.blocks = [{"type": "broken"}]
    with pytest.raises(StorefrontUnavailable, match="shape"):
        storefront.fetch_catalogue("shop")


def test_a_reference_kind_the_platform_does_not_know_is_a_bad_shape(catalogue):
    blocks = default_blocks()
    blocks[0]["refs"] = {"/x": "spaceship"}
    catalogue.blocks = blocks

    with pytest.raises(StorefrontUnavailable):
        storefront.fetch_catalogue("shop")


def test_a_held_copy_outlives_a_failed_revalidation(catalogue, settings):
    settings.BUILDER_CATALOGUE_REVALIDATE_SECONDS = 0
    held = storefront.fetch_catalogue("shop")
    catalogue.failure = requests.ConnectionError("down")

    assert storefront.fetch_catalogue("shop") is held


def test_no_base_url_is_unavailable_not_a_crash(settings):
    settings.NUXT_INTERNAL_BASE_URL = ""

    with (
        mock.patch("builder.storefront.requests.get") as get,
        pytest.raises(StorefrontUnavailable),
    ):
        storefront.fetch_catalogue("shop")

    get.assert_not_called()
