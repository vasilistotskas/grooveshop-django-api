# populate_user_account.py
import time

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from faker import Faker

from core.utils.password import generate_random_password
from helpers.seed import get_or_create_default_image

User = get_user_model()
faker = Faker()


class Command(BaseCommand):
    help = "Seed UserAccount model."

    def add_arguments(self, parser):
        parser.add_argument(
            "total_users",
            type=int,
            help="Indicates the number of users to be seeded.",
            default=100,
            nargs="?",
        )

    def handle(self, *args, **options):
        total_users = options["total_users"]
        total_time = 0
        start_time = time.time()

        if total_users < 1:
            self.stdout.write(
                self.style.WARNING("Total number of users must be greater than 0.")
            )
            return

        img = get_or_create_default_image("uploads/users/no_photo.jpg")

        objects_to_insert = []
        with transaction.atomic():
            for _ in range(total_users):
                first_name = faker.first_name()
                last_name = faker.last_name()
                email = faker.unique.email()
                phone = faker.phone_number()
                city = faker.city()
                zipcode = faker.zipcode()
                address = faker.street_address()
                place = faker.random_element(["Home", "Office", "Other"])
                birth_date = faker.date_of_birth()

                user_account_exists = User.objects.filter(email=email).exists()
                if user_account_exists:
                    continue

                user = User(
                    email=email,
                    password=generate_random_password(
                        use_digits=True, use_special_chars=True
                    ),
                    first_name=first_name,
                    last_name=last_name,
                    phone=phone,
                    city=city,
                    zipcode=zipcode,
                    address=address,
                    place=place,
                    birth_date=birth_date,
                    image=img,
                )

                objects_to_insert.append(user)
            User.objects.bulk_create(objects_to_insert)

        end_time = time.time()
        execution_time = end_time - start_time
        total_time += execution_time
        self.stdout.write(
            self.style.SUCCESS(
                f"{len(objects_to_insert)} UserAccount instances created successfully "
                f"in {execution_time:.2f} seconds."
            )
        )
