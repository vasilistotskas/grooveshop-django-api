"""Tenant-token scoping of the read-only Meilisearch search client.

The engine-level guarantee: search traffic for a tenant schema runs on
a JWT whose search rules cover ONLY ``{schema}__*`` indexes, so even a
mis-prefixed index name in query code cannot read another tenant's
data. These tests exercise the client-selection logic with the SDK
mocked — no live Meilisearch needed.
"""

from __future__ import annotations

from unittest import mock

from meili._client import Client
from meili._settings import _MeiliSettings


def _make_client(search_key: str, master_key: str = "master") -> Client:
    settings = mock.Mock(spec=_MeiliSettings)
    settings.https = False
    settings.host = "localhost"
    settings.port = "7700"
    settings.master_key = master_key
    settings.search_key = search_key
    settings.timeout = 5
    settings.sync = False
    with mock.patch("meili._client._Client"):
        return Client(settings)


def test_public_schema_uses_unscoped_client():
    client = _make_client(search_key="searchkey")
    assert client.search_client_for_schema("public") is client.search_client
    assert client.search_client_for_schema("") is client.search_client


def test_master_key_fallback_uses_unscoped_client():
    # Dev/CI: no dedicated search key — tenant tokens cannot be minted
    # from a master key, so the unscoped client serves (documented).
    client = _make_client(search_key="master", master_key="master")
    assert client.search_client_for_schema("acme") is client.search_client


def test_tenant_schema_gets_scoped_token_client(settings):
    settings.MEILISEARCH = {**settings.MEILISEARCH, "OFFLINE": False}
    client = _make_client(search_key="searchkey")
    client.client.get_key.return_value = mock.Mock(
        uid="123e4567-e89b-42d3-a456-426614174000"
    )
    client.client.generate_tenant_token.return_value = "jwt-acme"

    with mock.patch("meili._client._Client") as client_cls:
        scoped = client.search_client_for_schema("acme")

    # Token minted with rules covering ONLY this tenant's indexes.
    rules = client.client.generate_tenant_token.call_args.args[1]
    assert rules == {"acme__*": {}}
    client_cls.assert_called_once()
    assert client_cls.call_args.args[1] == "jwt-acme"
    assert scoped is client_cls.return_value

    # Cached: a second call within the refresh window mints nothing new.
    with mock.patch("meili._client._Client"):
        again = client.search_client_for_schema("acme")
    assert again is scoped
    assert client.client.generate_tenant_token.call_count == 1


def test_distinct_schemas_get_distinct_clients(settings):
    settings.MEILISEARCH = {**settings.MEILISEARCH, "OFFLINE": False}
    client = _make_client(search_key="searchkey")
    client.client.get_key.return_value = mock.Mock(
        uid="123e4567-e89b-42d3-a456-426614174000"
    )
    client.client.generate_tenant_token.side_effect = ["jwt-a", "jwt-b"]

    with mock.patch("meili._client._Client") as client_cls:
        client_cls.side_effect = lambda *a, **k: mock.Mock()
        a = client.search_client_for_schema("tenant_a")
        b = client.search_client_for_schema("tenant_b")

    assert a is not b
    rules = [
        call.args[1]
        for call in client.client.generate_tenant_token.call_args_list
    ]
    assert rules == [{"tenant_a__*": {}}, {"tenant_b__*": {}}]
