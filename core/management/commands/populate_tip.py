# populate_tip.py
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from faker import Faker

from helpers.seed import get_or_create_default_image
from tip.enum.tip_enum import TipKindEnum
from tip.models import Tip

faker = Faker()


class Command(BaseCommand):
    help = "Seed Tip model."

    def add_arguments(self, parser):
        parser.add_argument(
            "total_tips",
            type=int,
            help="Indicates the number of tips to be seeded.",
            default=10,
            nargs="?",
        )

    def handle(self, *args, **options):
        total_tips = options["total_tips"]
        total_time = 0
        start_time = time.time()
        available_languages = [
            lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
        ]

        if total_tips < 1:
            self.stdout.write(
                self.style.WARNING("Total number of tips must be greater than 0.")
            )
            return

        img = get_or_create_default_image("uploads/tips/no_photo.jpg")

        if not available_languages:
            self.stdout.write(self.style.ERROR("No languages found."))
            return

        tip_kind_choices = [choice[0] for choice in TipKindEnum.choices]

        objects_to_insert = []
        with transaction.atomic():
            for _ in range(total_tips):
                kind = faker.random_element(tip_kind_choices)
                active = faker.boolean(chance_of_getting_true=80)

                tip = Tip(kind=kind, icon=img, active=active)
                objects_to_insert.append(tip)
            Tip.objects.bulk_create(objects_to_insert)

            for tip in objects_to_insert:
                for lang in available_languages:
                    lang_seed = hash(f"{kind}{active}{lang}")
                    faker.seed_instance(lang_seed)
                    title = faker.word()
                    content = faker.text(max_nb_chars=500)
                    url = faker.url()
                    tip.set_current_language(lang)
                    tip.title = title
                    tip.content = content
                    tip.url = url
                    tip.save()

        end_time = time.time()
        execution_time = end_time - start_time
        total_time += execution_time
        self.stdout.write(
            self.style.SUCCESS(
                f"{len(objects_to_insert)} Tip instances created successfully "
                f"in {execution_time:.2f} seconds."
            )
        )
