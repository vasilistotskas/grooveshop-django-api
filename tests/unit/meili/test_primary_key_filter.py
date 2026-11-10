"""Delete by filter on the primary key: the payload, the filter, the client."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from meili._client import Client
from meili.dataclasses import MeiliIndexSettings
from meili.filters import primary_key_filter, quote_value
from product.models.product import ProductTranslation


def test_the_payload_makes_the_primary_key_filterable_by_equality_only():
    settings = MeiliIndexSettings(
        filterable_fields=["active"], facet_search=True, primary_key="pk"
    )

    assert settings.filterable_attributes_payload() == [
        {
            "attributePatterns": ["active"],
            "features": {
                "facetSearch": True,
                "filter": {"equality": True, "comparison": False},
            },
        },
        {
            "attributePatterns": ["pk"],
            "features": {
                "facetSearch": False,
                "filter": {"equality": True, "comparison": False},
            },
        },
    ]


def test_the_primary_key_is_added_even_with_no_filterable_fields():
    payload = MeiliIndexSettings(
        primary_key="pk"
    ).filterable_attributes_payload()

    assert [entry["attributePatterns"] for entry in payload] == [["pk"]]


def test_a_primary_key_already_filterable_is_not_listed_twice():
    settings = MeiliIndexSettings(
        filterable_fields=["pk", "active"], primary_key="pk"
    )

    patterns = [
        pattern
        for entry in settings.filterable_attributes_payload()
        for pattern in entry["attributePatterns"]
    ]
    assert patterns.count("pk") == 1


def test_a_model_settings_carry_its_primary_key():
    wanted = ProductTranslation.get_meili_settings()

    assert wanted.primary_key == ProductTranslation._meilisearch["primary_key"]
    assert any(
        wanted.primary_key in entry["attributePatterns"]
        for entry in wanted.filterable_attributes_payload()
    )


def test_the_filter_quotes_every_value():
    assert primary_key_filter("pk", [1, "2"]) == 'pk IN ["1", "2"]'


@pytest.mark.parametrize(
    ("value", "quoted"),
    [
        ('a"b', '"a\\"b"'),
        ("a\\b", '"a\\\\b"'),
        ('x"] OR pk != "', '"x\\"] OR pk != \\""'),
        ("trailing\\", '"trailing\\\\"'),
        ("café 'q'", "\"café 'q'\""),
    ],
)
def test_a_value_cannot_close_its_quote(value, quoted):
    assert quote_value(value) == quoted


@pytest.mark.parametrize("name", ["pk id", "pk]", "", "1pk", 'pk"'])
def test_an_attribute_name_that_is_not_an_identifier_is_refused(name):
    with pytest.raises(ValueError, match="attribute"):
        primary_key_filter(name, ["1"])


def test_an_empty_id_list_is_refused():
    with pytest.raises(ValueError, match="ids"):
        primary_key_filter("pk", [])


def test_the_client_deletes_by_filter_never_by_ids():
    client = Client.__new__(Client)
    index = MagicMock()
    client.client = MagicMock()
    client.client.index.return_value = index

    client.delete_documents_by_primary_key("idx", "pk", ["1", "2"])

    client.client.index.assert_called_once_with("idx")
    index.delete_documents.assert_called_once_with(filter='pk IN ["1", "2"]')


def test_no_code_deletes_by_ids():
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[3]
    pattern = re.compile(r"delete_documents\(\s*+(?!filter=)\S")
    offenders = [
        str(path.relative_to(root))
        for folder in ("meili", "product", "blog", "search", "tests/utils")
        for path in (root / folder).rglob("*.py")
        if pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
