import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


@pytest.mark.django_db
def test_alt_text_is_an_additive_nullable_column():
    column = next(
        c
        for c in connection.introspection.get_table_description(
            connection.cursor(), "product_productimage_translation"
        )
        if c.name == "alt_text"
    )

    assert column.null_ok
    assert column.display_size == 255


@pytest.mark.django_db
def test_the_migration_only_adds():
    loader = MigrationExecutor(connection).loader
    migration = loader.get_migration("product", "0062_productimage_alt_text")

    assert [type(op).__name__ for op in migration.operations] == ["AddField"]
