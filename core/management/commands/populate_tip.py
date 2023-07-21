# populate_tip.py
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

        if total_tips < 1:
            self.stdout.write(
                self.style.WARNING("Total number of tips must be greater than 0.")
            )
            return

        img = get_or_create_default_image("uploads/tips/no_photo.jpg")

        available_languages = [
            lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
        ]

        tip_kind_choices = [choice[0] for choice in TipKindEnum.choices()]

        created_tips = []
        with transaction.atomic():
            for _ in range(total_tips):
                kind = faker.random_element(tip_kind_choices)
                active = faker.boolean(chance_of_getting_true=80)

                # Create a new Tip object
                tip = Tip.objects.create(kind=kind, icon=img, active=active)

                for lang in available_languages:
                    faker.seed_instance(lang)
                    title = faker.word()
                    content = faker.text(max_nb_chars=500)
                    url = faker.url()
                    tip.set_current_language(lang)
                    tip.title = title
                    tip.content = content
                    tip.url = url
                    tip.save()

                created_tips.append(tip)

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully seeded {len(created_tips)} Tip instances."
            )
        )
