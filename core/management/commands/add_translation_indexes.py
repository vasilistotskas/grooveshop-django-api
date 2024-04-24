# add_translation_indexes.py
import time

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = "Adds custom Gin indexes to all translation fields."

    def handle(self, *args, **options):
        commands = [
            "add_product_translation_indexes",
            "add_blog_post_translation_indexes",
        ]

        total_time = 0

        for command_name in commands:
            self.stdout.write(f"Running {command_name}...")
            start_time = time.time()

            try:
                with transaction.atomic():
                    call_command(command_name, *args, **options)
            except Exception as e:
                self.stderr.write(
                    self.style.ERROR(f"{command_name} failed with error: {e}")
                )
                continue

            end_time = time.time()
            execution_time = end_time - start_time
            total_time += execution_time
            self.stdout.write(
                self.style.SUCCESS(
                    f"{command_name} completed successfully in {execution_time:.2f} seconds."
                )
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"All commands completed successfully in {total_time:.2f} seconds."
            )
        )
