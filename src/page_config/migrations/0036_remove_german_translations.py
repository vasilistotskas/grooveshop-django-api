from django.db import migrations

from core.utils.i18n import delete_translations_in


class Migration(migrations.Migration):
    """German is no longer a language of the platform: drop its rows."""

    dependencies = [("page_config", "0035_location_map_reads_store_geo")]

    operations = [
        migrations.RunPython(
            delete_translations_in(
                "de",
                "page_config.PageLayoutTranslation",
                "page_config.NavigationColumnTranslation",
                "page_config.NavigationLinkTranslation",
                "page_config.ContentPageTranslation",
            ),
            migrations.RunPython.noop,
        ),
    ]
