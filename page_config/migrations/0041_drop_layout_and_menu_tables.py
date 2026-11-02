"""Retire page_config's layouts and menus: release N+1, the contract step.

0040 took the models out of Django's state and left their tables for the
release that was still serving while it rolled out. That release is gone
(``contract_of`` refuses this migration until 0040 was applied by an
earlier deploy), nothing reads the tables, so they are dropped, the
translation tables first. The content types of the eight models go too,
and with them their permissions, so no permission picker offers a model
that does not exist.

Irreversible on purpose (no reverse operations, so Django refuses to
unapply it): the rows are gone with the tables, and a no-op reverse would
let a rollback walk on into 0040's reverse, which needs the link table.
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
        ),
        migrations.RunPython(delete_content_types),
    ]
