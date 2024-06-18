from django.core.management.base import BaseCommand
from django.db import connections


class Command(BaseCommand):
    help = "Adds custom Gin indexes to the ProductTranslation model"

    fields = {
        "search_vector": {
            "opclass": None,
        },
        "search_document": {
            "opclass": "gin_trgm_ops",
        },
        "name": {
            "opclass": "gin_trgm_ops",
        },
        "description": {
            "opclass": "gin_trgm_ops",
        },
    }

    def remove_index(self, cursor, index_name):
        cursor.execute(f"DROP INDEX IF EXISTS {index_name};")

    def add_index(self, cursor, index_name, table_name, fields):
        fields_string = ", ".join(
            [f"{field} {self.fields[field]['opclass']}" if self.fields[field]["opclass"] else field for field in fields]
        )
        cursor.execute(
            f"""
            CREATE INDEX IF NOT EXISTS {index_name}
            ON {table_name} USING GIN ({fields_string});
            """
        )

    def handle(self, *args, **options):
        with connections["default"].cursor() as cursor:
            for field, props in self.fields.items():
                index_name = f"product_product_translation_{field}_gin_idx"
                self.remove_index(cursor, index_name)

            for field, props in self.fields.items():
                index_name = f"product_product_translation_{field}_gin_idx"
                self.add_index(
                    cursor,
                    index_name,
                    "product_product_translation",
                    [field],
                )
                self.stdout.write(self.style.SUCCESS(f"Successfully added trigram index for {field}"))

            self.add_index(
                cursor,
                "product_product_translation_combined_gin_idx",
                "product_product_translation",
                ["name", "description"],
            )
            self.stdout.write(self.style.SUCCESS("Successfully added combined trigram index for name and description"))
