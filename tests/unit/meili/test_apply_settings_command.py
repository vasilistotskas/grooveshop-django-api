"""The ``meilisearch_apply_settings`` command is deploy infrastructure:
the grooveshop-infrastructure PreSync job runs it on every rollout. This
guard exists because the command was once deleted as "dead code" - its
only consumer lives in another repository - which broke the deploy
pipeline's prepare hook.
"""

from unittest.mock import patch

from django.core.management import call_command


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
