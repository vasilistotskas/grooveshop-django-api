# populate_user_account.py
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import IntegrityError
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

        if total_users < 1:
            self.stdout.write(
                self.style.WARNING("Total number of users must be greater than 0.")
            )
            return

        img = get_or_create_default_image("uploads/users/no_photo.jpg")

        created_users = []
        with transaction.atomic():
            for _ in range(total_users):
                first_name = faker.first_name()
                last_name = faker.last_name()
                email = faker.email()
                phone = faker.phone_number()
                city = faker.city()
                zipcode = faker.zipcode()
                address = faker.street_address()
                place = faker.random_element(["Home", "Office", "Other"])
                birth_date = faker.date_of_birth()

                # Create a new UserAccount object
                try:
                    user = User.objects.create_user(
                        email=email,
                        password=generate_random_password(),
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
                except IntegrityError:
                    # If the email is already in use, generate a new unique email
                    email = faker.unique.email()
                    user = User.objects.create_user(
                        email=email,
                        password=generate_random_password(),
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

                created_users.append(user)

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully seeded {len(created_users)} UserAccount instances."
            )
        )
