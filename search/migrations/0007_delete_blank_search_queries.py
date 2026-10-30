from django.db import migrations

from core.retention import delete_in_batches


def delete_blank_queries(apps, schema_editor):
    """Delete the listing loads that were recorded as searches.

    ``SearchAnalyticsMiddleware`` no longer records a request with no
    query text; every earlier one is a row no reader uses (the dashboard
    and the views exclude them). ``SearchClick`` rows of such a query go
    with it through the cascade. django-tenants runs this once per store
    schema.
    """
    SearchQuery = apps.get_model("search", "SearchQuery")
    delete_in_batches(SearchQuery.objects.filter(query__regex=r"^\s*$"))


class Migration(migrations.Migration):
    dependencies = [
        ("search", "0006_searchquery_language_code_help_text"),
    ]

    operations = [
        migrations.RunPython(
            delete_blank_queries,
            migrations.RunPython.noop,
            elidable=True,
        ),
    ]
