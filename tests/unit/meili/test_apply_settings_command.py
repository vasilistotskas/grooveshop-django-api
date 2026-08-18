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
