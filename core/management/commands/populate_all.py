# seed_all.py
import time

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = "Seed all models in the specified order."

    def handle(self, *args, **options):
        seed_commands = [
            "populate_user_account",
            "populate_country",
            "populate_region",
            "populate_pay_way",
            "populate_vat",
            "populate_product_category",
            "populate_product",
            "populate_product_image",
            "populate_product_review",
            "populate_product_favourite",
            "populate_slider",
            "populate_tip",
            "populate_blog_author",
            "populate_blog_category",
            "populate_blog_tag",
            "populate_blog_post",
            "populate_blog_comment",
            "populate_user_address",
            "populate_order",
        ]

        total_time = 0

        for command_name in seed_commands:
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
                f"All seed commands completed successfully in {total_time:.2f} seconds."
            )
        )
