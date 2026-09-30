"""Behaviour of the ``IndexMixin`` base shared by every indexed model.

Covers ``meili/models.py``: tenant-prefixed index names, geo settings,
``update_meili_settings`` (index creation order, task failure, flush),
``meili_serialize`` / ``_serialize_value`` edge cases, ``meili_geo``'s
guard and the ``meilisearch`` descriptor. Exercised through
``ProductTranslation``; the Meilisearch client is the only thing mocked.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest
from django.db import connection

from meili.exceptions import MeiliSettingsError
from meili.querysets import IndexQuerySet
from product.factories.product import ProductFactory
from product.models.product import ProductTranslation


class TestIndexName:
    def test_public_schema_uses_the_bare_base_name(self, monkeypatch):
        monkeypatch.setattr(connection, "schema_name", "public")

        assert ProductTranslation.get_meili_index_name() == (
            "ProductTranslation"
        )

    def test_tenant_schema_prefixes_the_base_name(self, monkeypatch):
        monkeypatch.setattr(connection, "schema_name", "acme")

        assert ProductTranslation.get_meili_index_name() == (
            "acme__ProductTranslation"
        )


class TestMeiliSettings:
    def test_geo_support_prepends_geo_to_filterable_and_sortable(self):
        base = ProductTranslation.get_meili_settings()

        with patch.object(
            ProductTranslation.MeiliMeta, "supports_geo", True, create=True
        ):
            geo = ProductTranslation.get_meili_settings()

        assert geo.filterable_fields == ["_geo", *base.filterable_fields]
        assert geo.sortable_fields == ["_geo", *base.sortable_fields]
        assert geo.searchable_fields == base.searchable_fields

    def test_unset_field_lists_become_none(self):
        with (
            patch.object(
                ProductTranslation.MeiliMeta, "displayed_fields", None
            ),
            patch.object(ProductTranslation.MeiliMeta, "sortable_fields", ()),
        ):
            settings = ProductTranslation.get_meili_settings()

        assert settings.displayed_fields is None
        assert settings.sortable_fields is None


class TestUpdateMeiliSettings:
    @staticmethod
    def _client(tasks, statuses):
        client = MagicMock()
        client.tasks = tasks
        client.wait_for_task.side_effect = [
            SimpleNamespace(status=status, error="boom") for status in statuses
        ]
        return client

    def test_creates_index_with_primary_key_before_applying_settings(self):
        client = self._client(
            [SimpleNamespace(task_uid=11), SimpleNamespace(uid=12)],
            ["succeeded", "succeeded"],
        )

        with patch("meili.models._client", client):
            ProductTranslation.update_meili_settings()

        assert client.mock_calls[:2] == [
            call.create_index("ProductTranslation", "pk"),
            call.with_settings(
                index_name="ProductTranslation",
                index_settings=ProductTranslation.get_meili_settings(),
            ),
        ]
        assert client.wait_for_task.call_args_list == [call(11), call(12)]
        client.flush_tasks.assert_called_once_with()

    def test_task_without_uid_is_not_waited_on(self):
        client = self._client([SimpleNamespace(task_uid=None, uid=None)], [])

        with patch("meili.models._client", client):
            ProductTranslation.update_meili_settings()

        client.wait_for_task.assert_not_called()
        client.flush_tasks.assert_called_once_with()

    def test_failed_settings_task_raises_and_keeps_tasks(self):
        client = self._client([SimpleNamespace(task_uid=11)], ["failed"])

        with (
            patch("meili.models._client", client),
            pytest.raises(
                MeiliSettingsError, match="Failed to update settings: boom"
            ),
        ):
            ProductTranslation.update_meili_settings()

        client.flush_tasks.assert_not_called()

    def test_no_pending_tasks_skips_wait_and_flush(self):
        client = self._client([], [])

        with patch("meili.models._client", client):
            ProductTranslation.update_meili_settings()

        client.wait_for_task.assert_not_called()
        client.flush_tasks.assert_not_called()


class TestSerializeValue:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, None),
            ("text", "text"),
            (3, 3),
            (2.5, 2.5),
            (False, False),
            (
                datetime.datetime(2026, 9, 30, 12, 0, tzinfo=datetime.UTC),
                "2026-09-30T12:00:00+00:00",
            ),
            (datetime.date(2026, 9, 30), "2026-09-30"),
            (Decimal("9.90"), "9.90"),
            (
                ("a", datetime.date(2026, 1, 2)),
                ["a", "2026-01-02"],
            ),
            (
                {"when": datetime.date(2026, 1, 2), "n": [Decimal(1)]},
                {"when": "2026-01-02", "n": ["1"]},
            ),
        ],
    )
    def test_value_is_made_json_safe(self, value, expected):
        assert ProductTranslation()._serialize_value(value) == expected


@pytest.mark.django_db
class TestMeiliSerialize:
    def test_skips_internal_fields_and_nulls_failing_getters(self):
        translation = ProductFactory(
            num_images=0, num_reviews=0
        ).translations.first()

        def _attribute_error(obj):
            raise AttributeError("no category")

        def _type_error(obj):
            raise TypeError("bad arithmetic")

        additional = {
            "missing_relation": _attribute_error,
            "broken_math": _type_error,
            "stamp": lambda obj: datetime.date(2026, 9, 30),
        }
        with (
            patch.object(
                ProductTranslation.MeiliMeta,
                "filterable_fields",
                ("_geo", "name", "no_such_attribute"),
            ),
            patch.object(
                ProductTranslation.MeiliMeta, "displayed_fields", None
            ),
            patch.object(
                ProductTranslation.MeiliMeta, "searchable_fields", None
            ),
            patch.object(
                ProductTranslation,
                "get_additional_meili_fields",
                classmethod(lambda cls: additional),
            ),
        ):
            data = translation.meili_serialize()

        assert data == {
            "name": translation.name,
            "no_such_attribute": None,
            "missing_relation": None,
            "broken_math": None,
            "stamp": "2026-09-30",
        }

    def test_include_pk_in_search_with_the_default_primary_key(self):
        """``primary_key`` defaults to ``"pk"``, which is an alias, not a
        field name: ``_meta.get_field("pk")`` raises. A MeiliMeta that
        does not declare ``primary_key`` at all (every real one) must
        also fall back to it, as ``_get_document_pk`` does.
        """
        translation = ProductFactory(
            num_images=0, num_reviews=0
        ).translations.first()

        with patch.object(
            ProductTranslation.MeiliMeta,
            "include_pk_in_search",
            True,
            create=True,
        ):
            data = translation.meili_serialize()

        assert data["pk"] == str(translation.pk)

    def test_include_pk_in_search_with_a_named_primary_key(self):
        translation = ProductFactory(
            num_images=0, num_reviews=0
        ).translations.first()

        with (
            patch.object(
                ProductTranslation.MeiliMeta,
                "include_pk_in_search",
                True,
                create=True,
            ),
            patch.object(
                ProductTranslation.MeiliMeta,
                "primary_key",
                "id",
                create=True,
            ),
        ):
            data = translation.meili_serialize()

        assert data["id"] == str(translation.pk)


class TestGeoAndDescriptor:
    def test_meili_geo_must_be_overridden(self):
        with pytest.raises(
            NotImplementedError,
            match="ProductTranslation has supports_geo=True",
        ):
            ProductTranslation().meili_geo()

    def test_descriptor_returns_a_fresh_queryset_bound_to_the_model(self):
        first = ProductTranslation.meilisearch
        second = ProductTranslation.meilisearch

        assert isinstance(first, IndexQuerySet)
        assert first is not second
        assert first.model is ProductTranslation
