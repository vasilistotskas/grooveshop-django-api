from unittest.mock import MagicMock, patch

from django_tenants.utils import schema_context


def _index(uid):
    idx = MagicMock()
    idx.uid = uid
    return idx


ALL_INDEXES = [
    _index("ProductTranslation"),
    _index("BlogPostTranslation"),
    _index("webside__ProductTranslation"),
    _index("webside__BlogPostTranslation"),
    _index("aurora__ProductTranslation"),
]


def test_drop_deletes_only_active_tenant_schema_indexes():
    """A tenant-schema run must not touch other tenants' indexes or the
    public (unprefixed) ones — the old instance-wide listing meant
    ``--all-tenants`` left only the last schema's indexes alive."""
    from meili.management.commands.meilisearch_drop import Command

    with patch(
        "meili.management.commands.meilisearch_drop._client"
    ) as mock_client:
        mock_client.get_indexes.return_value = ALL_INDEXES

        with schema_context("webside"):
            Command()._handle_for_schema(force=True, recreate=False)

        deleted = [
            c.args[0] for c in mock_client.client.delete_index.call_args_list
        ]
        assert sorted(deleted) == [
            "webside__BlogPostTranslation",
            "webside__ProductTranslation",
        ]


def test_drop_on_public_schema_deletes_only_unprefixed_indexes():
    from meili.management.commands.meilisearch_drop import Command

    with patch(
        "meili.management.commands.meilisearch_drop._client"
    ) as mock_client:
        mock_client.get_indexes.return_value = ALL_INDEXES

        with schema_context("public"):
            Command()._handle_for_schema(force=True, recreate=False)

        deleted = [
            c.args[0] for c in mock_client.client.delete_index.call_args_list
        ]
        assert sorted(deleted) == [
            "BlogPostTranslation",
            "ProductTranslation",
        ]
