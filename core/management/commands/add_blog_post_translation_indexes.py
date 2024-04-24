from django.core.management.base import BaseCommand
from django.db import connections


class Command(BaseCommand):
    help = "Adds custom Gin indexes to the BlogPostTranslation model"

    fields = {
        "search_vector": {
            "opclass": None,
        },
        "search_document": {
            "opclass": "gin_trgm_ops",
        },
        "title": {
            "opclass": "gin_trgm_ops",
        },
        "subtitle": {
            "opclass": "gin_trgm_ops",
        },
        "body": {
            "opclass": "gin_trgm_ops",
        },
    }

    def remove_index(self, cursor, index_name):
        cursor.execute(f"DROP INDEX IF EXISTS {index_name};")

    def add_index(self, cursor, index_name, table_name, fields):
        fields_string = ", ".join(
            [
                f"{field} {self.fields[field]['opclass']}"
                if self.fields[field]["opclass"]
                else field
                for field in fields
            ]
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
                index_name = f"blog_blogpost_translation_{field}_gin_idx"
                self.remove_index(cursor, index_name)

            for field, props in self.fields.items():
                index_name = f"blog_blogpost_translation_{field}_gin_idx"
                self.add_index(
                    cursor,
                    index_name,
                    "blog_blogpost_translation",
                    [field],
                )
                self.stdout.write(
                    self.style.SUCCESS(f"Successfully added trigram index for {field}")
                )

            self.add_index(
                cursor,
                "blog_blogpost_translation_combined_gin_idx",
                "blog_blogpost_translation",
                ["title", "subtitle", "body"],
            )
            self.stdout.write(
                self.style.SUCCESS(
                    "Successfully added combined trigram index for title, subtitle and body"
                )
            )
