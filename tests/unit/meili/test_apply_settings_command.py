"""The ``meilisearch_apply_settings`` command is deploy infrastructure:
the grooveshop-infrastructure PreSync job runs it on every rollout. This
guard exists because the command was once deleted as "dead code" - its
only consumer lives in another repository - which broke the deploy
pipeline's prepare hook.
"""

from unittest.mock import patch

from django.core.management import call_command


def test_update_meili_settings_ensures_index_with_primary_key_first():
    """The settings call must be preceded by create_index — a settings
    task auto-creates a missing index WITHOUT a primaryKey, after which
    every document addition fails. This is the exact order of events on
    a brand-new tenant (PreSync apply_settings before any sync)."""
    from unittest.mock import MagicMock, call, patch

    from product.models.product import ProductTranslation

    with patch("meili.models._client") as mock_client:
        manager = MagicMock()
        manager.attach_mock(mock_client.create_index, "create_index")
        manager.attach_mock(mock_client.with_settings, "with_settings")
        mock_client.tasks = []

        ProductTranslation.update_meili_settings()

        index_name = ProductTranslation.get_meili_index_name()
        primary_key = ProductTranslation._meilisearch["primary_key"]
        assert manager.mock_calls[0] == call.create_index(
            index_name, primary_key
        )
        assert manager.mock_calls[1] == call.with_settings(
            index_name=index_name,
            index_settings=ProductTranslation.get_meili_settings(),
        )


def test_apply_settings_command_exists_and_updates_both_indexes():
    with (
        patch(
            "product.models.product.ProductTranslation.update_meili_settings"
        ) as product_update,
        patch(
            "blog.models.post.BlogPostTranslation.update_meili_settings"
        ) as blog_update,
    ):
        call_command("meilisearch_apply_settings")
    assert product_update.called
    assert blog_update.called


def test_a_failing_tenant_does_not_stop_the_remaining_ones():
    """The PreSync hook must attempt every store before it gives up.

    ``self._failures`` is built once per RUN, but the raise used to sit
    in the per-schema handler — so the first tenant whose index update
    failed aborted the loop, and every store after it was left on the
    settings drift this command exists to prevent (the drift that "once
    made every ?sort= product query 500"). Worse, the list is shared:
    once tenant A had failed, tenant B raised on A's entry even if B
    itself succeeded.
    """
    import pytest
    from django.core.management.base import CommandError

    from meili.management.commands import meilisearch_apply_settings

    with (
        patch.object(
            meilisearch_apply_settings.Command,
            "get_tenant_schemas",
            return_value=["alpha", "beta"],
        ),
        patch(
            "product.models.product.ProductTranslation.update_meili_settings",
            side_effect=[RuntimeError("boom"), None],
        ) as product_update,
        patch(
            "blog.models.post.BlogPostTranslation.update_meili_settings"
        ) as blog_update,
        pytest.raises(CommandError) as excinfo,
    ):
        call_command("meilisearch_apply_settings")

    assert product_update.call_count == 2, (
        "aborted before the second tenant was attempted"
    )
    assert blog_update.call_count == 2
    assert "boom" in str(excinfo.value)


def test_the_failure_names_the_schema_that_is_still_drifted():
    """An entry naming only the index does not say which store to fix."""
    from contextlib import contextmanager

    import pytest
    from django.core.management.base import CommandError
    from django.db import connection

    from meili.management.commands import meilisearch_apply_settings

    @contextmanager
    def fake_schema_context(schema):
        # Assigning schema_name directly is only safe because every
        # collaborator here is patched, so nothing re-enters a real
        # schema_context and strands the value; it is restored either way.
        previous = connection.schema_name
        connection.schema_name = schema
        try:
            yield
        finally:
            connection.schema_name = previous

    with (
        patch.object(
            meilisearch_apply_settings.Command,
            "get_tenant_schemas",
            return_value=["alpha", "beta"],
        ),
        patch("django_tenants.utils.schema_context", fake_schema_context),
        patch(
            "product.models.product.ProductTranslation.update_meili_settings",
            side_effect=RuntimeError("boom"),
        ),
        patch("blog.models.post.BlogPostTranslation.update_meili_settings"),
        pytest.raises(CommandError) as excinfo,
    ):
        call_command("meilisearch_apply_settings")

    message = str(excinfo.value)
    assert "alpha/ProductTranslation" in message
    assert "beta/ProductTranslation" in message


def test_an_unknown_index_argument_fails_the_command():
    """It used to print "Unknown index" and then exit 0, applying nothing."""
    import pytest
    from django.core.management.base import CommandError

    with (
        patch(
            "product.models.product.ProductTranslation.update_meili_settings"
        ) as product_update,
        patch(
            "blog.models.post.BlogPostTranslation.update_meili_settings"
        ) as blog_update,
        pytest.raises(CommandError, match="Unknown index"),
    ):
        call_command("meilisearch_apply_settings", "--index", "Nope")

    assert not product_update.called
    assert not blog_update.called
