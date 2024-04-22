from django.contrib.auth import get_user_model
from django.test import TestCase

from user.enum.address import FloorChoicesEnum
from user.enum.address import LocationChoicesEnum
from user.models.address import UserAddress


User = get_user_model()


class UserAddressModelTestCase(TestCase):
    user: User = None
    address: UserAddress = None

    def setUp(self):
        self.user = User.objects.create_user(
            email="test@test.com", password="test12345@!"
        )
        self.address = UserAddress.objects.create(
            user=self.user,
            title="Home",
            first_name="John",
            last_name="Doe",
            street="123 Main St",
            street_number="Apt 4B",
            city="Cityville",
            zipcode="12345",
            country=None,
            region=None,
            floor=FloorChoicesEnum.FIRST_FLOOR.value,
            location_type=LocationChoicesEnum.HOME.value,
            phone="123-456-7890",
            mobile_phone="987-654-3210",
            notes="Sample notes",
            is_main=True,
        )

    def test_fields(self):
        self.assertEqual(UserAddress.objects.count(), 1)
        self.assertEqual(self.address.user, self.user)
        self.assertEqual(self.address.title, "Home")
        self.assertEqual(self.address.first_name, "John")
        self.assertEqual(self.address.last_name, "Doe")
        self.assertEqual(self.address.street, "123 Main St")
        self.assertEqual(self.address.street_number, "Apt 4B")
        self.assertEqual(self.address.city, "Cityville")
        self.assertEqual(self.address.zipcode, "12345")
        self.assertEqual(self.address.country, None)
        self.assertEqual(self.address.region, None)
        self.assertEqual(self.address.floor, FloorChoicesEnum.FIRST_FLOOR.value)
        self.assertEqual(self.address.location_type, LocationChoicesEnum.HOME.value)
        self.assertEqual(self.address.phone, "123-456-7890")

    def test_str_representation(self):
        self.assertEqual(
            str(self.address),
            f"{self.address.title} - {self.address.first_name}"
            f" {self.address.last_name}, {self.address.city}",
        )

    def test_get_main_address(self):
        non_main_address_1 = UserAddress.objects.create(
            user=self.user,
            title="Main Address",
            is_main=False,
            first_name="John",
            last_name="Doe",
            street="123 Main St",
            street_number="Apt 4B",
            city="Cityville",
            zipcode="12345",
            country=None,
            region=None,
            floor=FloorChoicesEnum.FIRST_FLOOR.value,
            location_type=LocationChoicesEnum.HOME.value,
            phone="123-456-7890",
            mobile_phone="987-654-3210",
            notes="Sample notes",
        )
        non_main_address_2 = UserAddress.objects.create(
            user=self.user,
            title="Non-Main Address",
            is_main=False,
            first_name="John",
            last_name="Doe",
            street="123 Main St",
            street_number="Apt 4B",
            city="Cityville",
            zipcode="12345",
            country=None,
            region=None,
            floor=FloorChoicesEnum.FIRST_FLOOR.value,
            location_type=LocationChoicesEnum.HOME.value,
            phone="123-456-7890",
            mobile_phone="987-654-3210",
            notes="Sample notes",
        )
        address = UserAddress.get_main_address(self.user)

        self.assertTrue(address.is_main)
        self.assertFalse(non_main_address_1.is_main)
        self.assertFalse(non_main_address_2.is_main)

    def test_get_user_address_count(self):
        UserAddress.objects.create(
            user=self.user,
            title="Address 1",
            first_name="John",
            last_name="Doe",
            street="123 Main St",
            street_number="Apt 4B",
            city="Cityville",
            zipcode="12345",
            country=None,
            region=None,
            floor=FloorChoicesEnum.FIRST_FLOOR.value,
            location_type=LocationChoicesEnum.HOME.value,
            phone="123-456-7890",
            mobile_phone="987-654-3210",
            notes="Sample notes",
            is_main=False,
        )
        UserAddress.objects.create(
            user=self.user,
            title="Address 2",
            first_name="John",
            last_name="Doe",
            street="123 Main St",
            street_number="Apt 4B",
            city="Cityville",
            zipcode="12345",
            country=None,
            region=None,
            floor=FloorChoicesEnum.FIRST_FLOOR.value,
            location_type=LocationChoicesEnum.HOME.value,
            phone="123-456-7890",
            mobile_phone="987-654-3210",
            notes="Sample notes",
            is_main=False,
        )
        count = UserAddress.get_user_address_count(self.user)

        self.assertEqual(count, 3)

    def tearDown(self) -> None:
        super().tearDown()
        self.user.delete()
        self.address.delete()
