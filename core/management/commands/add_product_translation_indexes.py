from django.core.management.base import BaseCommand
from django.db import connections


class Command(BaseCommand):
    help = "Adds custom Gin indexes to the ProductTranslation model"

    def add_index(self, cursor, index_name, table_name, fields, opclass=None):
        field_string = ", ".join(
            [f"{field} {opclass}" if opclass else field for field in fields]
        )
        cursor.execute(
            f"""
            CREATE INDEX IF NOT EXISTS {index_name}
            ON {table_name} USING gin({field_string});
        """
        )

    def handle(self, *args, **options):
        with connections["default"].cursor() as cursor:
            self.add_index(
                cursor,
                "product_product_translation_search_document_idx",
                "product_product_translation",
                ["search_document"],
                "gin_trgm_ops",
            )
            self.stdout.write(
                self.style.SUCCESS(
                    "Successfully added index product_translation_search_document_idx"
                )
            )

            self.add_index(
                cursor,
                "product_product_translation_search_vector_idx",
                "product_product_translation",
                ["search_vector"],
            )
            self.stdout.write(
                self.style.SUCCESS(
                    "Successfully added index product_translation_search_vector_idx"
                )
            )

            self.add_index(
                cursor,
                "product_product_translation_search_gin",
                "product_product_translation",
                ["name", "description"],
                "gin_trgm_ops",
            )
            self.stdout.write(
                self.style.SUCCESS(
                    "Successfully added index product_translation_search_document_idx"
                )
            )
