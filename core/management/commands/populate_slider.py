# populate_slider.py
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from faker import Faker

from helpers.seed import get_or_create_default_image
from slider.models import Slide
from slider.models import Slider

faker = Faker()


class Command(BaseCommand):
    help = "Seed Slider and Slide models."

    def add_arguments(self, parser):
        parser.add_argument(
            "total_sliders",
            type=int,
            help="Indicates the number of sliders to be seeded.",
            default=10,
            nargs="?",
        )
        parser.add_argument(
            "slides_per_slider",
            type=int,
            help="Indicates the number of slides to be seeded per slider.",
            default=3,
            nargs="?",
        )

    def handle(self, *args, **options):
        total_sliders = options["total_sliders"]
        slides_per_slider = options["slides_per_slider"]
        total_time = 0
        start_time = time.time()
        available_languages = [
            lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
        ]

        if total_sliders < 1 or slides_per_slider < 1:
            self.stdout.write(
                self.style.WARNING(
                    "Total number of sliders and slides per slider must be greater than 0."
                )
            )
            return

        slider_img = get_or_create_default_image("uploads/sliders/no_photo.jpg")
        slide_img = get_or_create_default_image("uploads/slides/no_photo.jpg")

        if not available_languages:
            self.stdout.write(self.style.ERROR("No languages found."))
            return

        slider_objects_to_insert = []
        slides_objects_to_insert = []
        with transaction.atomic():
            for _ in range(total_sliders):
                slider = Slider(image=slider_img)
                slider_objects_to_insert.append(slider)
            Slider.objects.bulk_create(slider_objects_to_insert)

            for slider in slider_objects_to_insert:
                for lang in available_languages:
                    lang_seed = hash(f"{slider.id}{lang}")
                    faker.seed_instance(lang_seed)
                    name = faker.word()
                    url = faker.url()
                    title = faker.word()
                    description = faker.text()
                    slider.set_current_language(lang)
                    slider.name = name
                    slider.url = url
                    slider.title = title
                    slider.description = description
                    slider.save()

            for _ in range(slides_per_slider):
                discount = faker.pydecimal(left_digits=2, right_digits=2, positive=True)
                show_button = faker.boolean()
                date_start = faker.date_time_this_year(
                    before_now=True, after_now=False, tzinfo=timezone.utc
                )
                date_end = faker.date_time_this_year(
                    before_now=False, after_now=True, tzinfo=timezone.utc
                )

                slide = Slide(
                    slider=slider,
                    discount=discount,
                    show_button=show_button,
                    date_start=date_start,
                    date_end=date_end,
                    image=slide_img,
                )
                slides_objects_to_insert.append(slide)
            Slide.objects.bulk_create(slides_objects_to_insert)

            for slide in slider_objects_to_insert:
                for lang in available_languages:
                    lang_seed = hash(f"{slide.id}{lang}")
                    faker.seed_instance(lang_seed)
                    name = faker.word()
                    url = faker.url()
                    title = faker.word()
                    subtitle = faker.word()
                    description = faker.text()
                    button_label = faker.word()
                    slide.set_current_language(lang)
                    slide.name = name
                    slide.url = url
                    slide.title = title
                    slide.subtitle = subtitle
                    slide.description = description
                    slide.button_label = button_label
                    slide.save()

        end_time = time.time()
        execution_time = end_time - start_time
        total_time += execution_time
        self.stdout.write(
            self.style.SUCCESS(
                f"{len(slider_objects_to_insert)} Slider instances and "
                f"{len(slides_objects_to_insert)} Slide instances created successfully "
                f"in {execution_time:.2f} seconds."
            )
        )
