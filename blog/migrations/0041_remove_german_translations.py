from django.db import migrations

from core.utils.i18n import delete_translations_in


class Migration(migrations.Migration):
    """German is no longer a language of the platform: drop its rows."""

    dependencies = [("blog", "0040_field_verbose_names")]

    operations = [
        migrations.RunPython(
            delete_translations_in(
                "de",
                "blog.BlogAuthorTranslation",
                "blog.BlogPostTranslation",
                "blog.BlogCategoryTranslation",
                "blog.BlogCommentTranslation",
                "blog.BlogTagTranslation",
            ),
            migrations.RunPython.noop,
        ),
    ]
