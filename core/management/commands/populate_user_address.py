# populate_user_address.py
import time

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from faker import Faker

from user.enum.address import FloorChoicesEnum
from user.enum.address import LocationChoicesEnum
from user.models import UserAccount
from user.models.address import UserAddress

User = get_user_model()
faker = Faker()


class Command(BaseCommand):
    help = "Seed UserAddress model."

    def add_arguments(self, parser):
        parser.add_argument(
            "total_addresses",
            type=int,
            help="Indicates the number of user addresses to be seeded.",
            default=10,
            nargs="?",
        )

    def handle(self, *args, **options):
        total_time = 0
        start_time = time.time()
        total_addresses = options["total_addresses"]

        if total_addresses < 1:
            self.stdout.write(
                self.style.WARNING(
                    "Total number of user addresses must be greater than 0."
                )
            )
            return

        users: list[UserAccount] = list(User.objects.all())

        if not users:
            self.stdout.write(
                self.style.ERROR(
                    "Insufficient data. Aborting seeding UserAddress model."
                )
            )
            return

        floor_choices = [choice[0] for choice in FloorChoicesEnum.choices]
        location_choices = [choice[0] for choice in LocationChoicesEnum.choices]

        objects_to_insert: list[UserAddress] = []
        with transaction.atomic():
            for user in users:
                try:
                    user.user_address.get(is_main=True)
                    main_address_exists = True
                except UserAddress.DoesNotExist:
                    main_address_exists = False

                if not main_address_exists:
                    first_name = (
                        user.first_name if user.first_name else faker.first_name()
                    )
                    last_name = user.last_name if user.last_name else faker.last_name()
                    main_address = UserAddress(
                        user=user,
                        title="Main Address",
                        first_name=first_name,
                        last_name=last_name,
                        street=faker.street_name(),
                        street_number=faker.building_number(),
                        city=faker.city(),
                        zipcode=faker.zipcode(),
                        floor=faker.random_element(floor_choices),
                        location_type=faker.random_element(location_choices),
                        phone=faker.phone_number(),
                        mobile_phone=faker.phone_number(),
                        notes=faker.sentence(),
                        is_main=True,
                    )
                    objects_to_insert.append(main_address)

                for _ in range(total_addresses - 1):
                    address = UserAddress(
                        user=user,
                        title=faker.word().capitalize(),
                        first_name=faker.first_name(),
                        last_name=faker.last_name(),
                        street=faker.street_name(),
                        street_number=faker.building_number(),
                        city=faker.city(),
                        zipcode=faker.zipcode(),
                        floor=faker.random_element(floor_choices),
                        location_type=faker.random_element(location_choices),
                        phone=faker.phone_number(),
                        mobile_phone=faker.phone_number(),
                        notes=faker.sentence(),
                        is_main=False,
                    )
                    objects_to_insert.append(address)
            UserAddress.objects.bulk_create(objects_to_insert)

        end_time = time.time()
        execution_time = end_time - start_time
        total_time += execution_time
        self.stdout.write(
            self.style.SUCCESS(
                f"{len(objects_to_insert)} UserAddress instances created successfully in {execution_time:.2f} seconds."
            )
        )
