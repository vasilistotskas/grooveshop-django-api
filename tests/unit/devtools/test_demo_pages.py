"""The demo store's pages: ``devtools/demo_pages`` and ``seed_pages``.

The pages are builder documents exported from production's demo store, with
what differs per store (a product's id, an image's media path) left as a
placeholder the seeder resolves. The dataset is checked against the pinned
snapshot of the storefront's real catalogue, so a block change that breaks
a demo page fails here rather than on a rebuilt demo store; the seed itself
is validated again, references included, by ``seed_with_content``.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest
from django.test import TestCase

from builder import seeding
from builder.models import kind_of_key
from builder.seeding import SEED_CATALOGUE
from builder.storefront import parse_catalogue
from builder.validation import validate_document
from devtools import demo_store
from devtools.demo_catalogue import PRODUCTS
from product.factories.product import ProductFactory

CATALOGUE = parse_catalogue(
    json.loads(SEED_CATALOGUE.read_text(encoding="utf-8"))
)
LOCK = json.loads(
    (
        Path(demo_store.__file__).resolve().parent
        / "demo_assets"
        / "manifest.lock.json"
    ).read_text(encoding="utf-8")
)
SEEDS = seeding.load_seeds(demo_store.DEMO_PAGES)


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [s for item in value for s in _strings(item)]
    if isinstance(value, dict):
        return [s for item in value.values() for s in _strings(item)]
    return []


def _values_of(name: str, value) -> list:
    """Every value of a setting called *name*, at any depth."""
    if isinstance(value, list):
        return [found for item in value for found in _values_of(name, item)]
    if isinstance(value, dict):
        own = [value[name]] if name in value else []
        return own + [
            found for item in value.values() for found in _values_of(name, item)
        ]
    return []


def _resolved(seed: seeding.Seed) -> seeding.Seed:
    """The seed as a store would get it, with stand-in values."""
    with (
        mock.patch.object(demo_store, "ensure_asset", lambda key: key),
        mock.patch.object(
            demo_store, "media_path", lambda key: f"media/x/uploads/{key}.avif"
        ),
    ):
        ids = {slug: 1 for slug in demo_store._product_slugs(seed.data)}
        return seeding.Seed(
            key=seed.key,
            data=demo_store._resolve_placeholders(seed.data, ids),
            i18n=demo_store._resolve_placeholders(seed.i18n, ids),
        )


class TestTheDataset(TestCase):
    def test_the_pages_and_menus_of_the_showcase_are_all_there(self):
        assert {seed.key for seed in SEEDS} == {
            "template:home",
            "template:about",
            "template:contact",
            "template:feedback",
            "group:footer",
            "group:mobile",
        }

    def test_every_key_names_a_kind_of_document(self):
        for seed in SEEDS:
            assert kind_of_key(seed.key) is not None, seed.key

    def test_every_page_is_valid_against_the_real_catalogue(self):
        for seed in SEEDS:
            page = _resolved(seed)
            issues, validated = validate_document(
                key=page.key,
                data=page.data,
                i18n=page.i18n,
                catalogue=CATALOGUE,
                feature_on=lambda name: True,
            )
            assert issues == [], (seed.key, issues)
            assert validated is not None

    def test_no_value_of_one_store_is_left_in(self):
        """A media path names its schema and a product id its row: both
        would be another store's. They must be placeholders."""
        for seed in SEEDS:
            for value in _strings(seed.data) + _strings(seed.i18n):
                assert not value.startswith("media/"), (seed.key, value)
            for value in _values_of("product", seed.data):
                assert demo_store._PRODUCT_RE.match(str(value)), seed.key

    def test_every_asset_placeholder_is_a_committed_asset(self):
        keys = [
            key
            for seed in SEEDS
            for value in _strings(seed.data) + _strings(seed.i18n)
            for key in demo_store._ASSET_RE.findall(value)
        ]
        assert keys
        for key in keys:
            assert key in LOCK, key

    def test_every_product_placeholder_is_a_demo_product(self):
        slugs = {row.slug for row in PRODUCTS}
        named = set().union(
            *(demo_store._product_slugs(seed.data) for seed in SEEDS)
        )
        assert named
        assert named <= slugs


@pytest.mark.django_db
class TestSeedPages:
    @pytest.fixture
    def written(self):
        captured: dict[str, list[seeding.Seed]] = {}

        def fake(seeds, *, note):
            captured["seeds"] = seeds
            captured["note"] = note
            return mock.Mock()

        with (
            mock.patch.object(seeding, "seed_with_content", side_effect=fake),
            mock.patch.object(demo_store, "ensure_asset", lambda key: key),
            mock.patch.object(
                demo_store,
                "media_path",
                lambda key: f"media/demo/uploads/pages/{key}.avif",
            ),
        ):
            yield captured

    def test_placeholders_become_this_stores_values(self, written):
        product = ProductFactory(slug="demo-powerbank-20k")

        report = demo_store.seed_pages()

        assert report == {"pages_seeded": len(SEEDS)}
        assert written["note"] == demo_store.DEMO_PAGES_NOTE
        text = json.dumps([seed.data for seed in written["seeds"]])
        assert "{{" not in text
        assert f'"product": {product.pk}' in text
        assert "media/demo/uploads/pages/hero-audio.avif" in text

    def test_a_store_with_pages_of_its_own_is_left_alone(self, written):
        ProductFactory(slug="demo-powerbank-20k")

        with mock.patch.object(seeding, "seed_with_content", return_value=None):
            report = demo_store.seed_pages()

        assert report == {"pages_left_alone": len(SEEDS)}

    def test_a_missing_product_is_named(self, written):
        with pytest.raises(KeyError, match="demo-powerbank-20k"):
            demo_store.seed_pages()
