"""Retire page_config's layouts and menus: release N+1, the contract step.

0040 took the models out of Django's state and left their tables for the
release that was still serving while it rolled out. That release is gone
(``contract_of`` refuses this migration until 0040 was applied by an
earlier deploy), nothing reads the tables, so they are dropped, the
translation tables first. The content types of the eight models go too,
and with them their permissions, so no permission picker offers a model
that does not exist.

Not reversible: the rows are gone with the tables, and the code that
used them was removed by the release before this one.
"""

from django.db import migrations

TABLES = (
    "page_config_navigationlink_translation",
    "page_config_navigationcolumn_translation",
    "page_config_pagelayout_translation",
    "page_config_navigationlink",
    "page_config_navigationcolumn",
    "page_config_navigationmenu",
    "page_config_pagesection",
    "page_config_pagelayout",
)

MODELS = (
    "navigationlinktranslation",
    "navigationcolumntranslation",
    "pagelayouttranslation",
    "navigationlink",
    "navigationcolumn",
    "navigationmenu",
    "pagesection",
    "pagelayout",
)


def delete_content_types(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    ContentType.objects.using(schema_editor.connection.alias).filter(
        app_label="page_config", model__in=MODELS
    ).delete()


class Migration(migrations.Migration):
    contract_of = [("page_config", "0040_retire_layouts_and_menus_from_state")]

    dependencies = [
        ("page_config", "0040_retire_layouts_and_menus_from_state"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.RunSQL(
            "".join(f"DROP TABLE IF EXISTS {table} CASCADE;\n" for table in TABLES),
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RunPython(
            delete_content_types, reverse_code=migrations.RunPython.noop
        ),
    ]
