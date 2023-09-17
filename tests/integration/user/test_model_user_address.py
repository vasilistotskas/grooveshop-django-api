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
        # Create a sample user for testing
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
            country=None,  # You can set an actual country object here
            region=None,  # You can set an actual region object here
            floor=FloorChoicesEnum.FIRST_FLOOR.value,
            location_type=LocationChoicesEnum.HOME.value,
            phone="123-456-7890",
            mobile_phone="987-654-3210",
            notes="Sample notes",
            is_main=True,
        )

    def test_fields(self):
        # Test if the fields are saved correctly
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

    def test_verbose_names(self):
        # Test verbose names for fields
        self.assertEqual(UserAddress._meta.get_field("user").verbose_name, "user")
        self.assertEqual(UserAddress._meta.get_field("title").verbose_name, "Title")
        self.assertEqual(
            UserAddress._meta.get_field("first_name").verbose_name, "First Name"
        )
        self.assertEqual(
            UserAddress._meta.get_field("last_name").verbose_name, "Last Name"
        )
        self.assertEqual(UserAddress._meta.get_field("street").verbose_name, "Street")
        self.assertEqual(
            UserAddress._meta.get_field("street_number").verbose_name, "Street Number"
        )
        self.assertEqual(UserAddress._meta.get_field("city").verbose_name, "City")
        self.assertEqual(
            UserAddress._meta.get_field("zipcode").verbose_name, "Zip Code"
        )
        self.assertEqual(UserAddress._meta.get_field("country").verbose_name, "country")
        self.assertEqual(UserAddress._meta.get_field("region").verbose_name, "region")
        self.assertEqual(UserAddress._meta.get_field("floor").verbose_name, "Floor")
        self.assertEqual(
            UserAddress._meta.get_field("location_type").verbose_name, "Location Type"
        )
        self.assertEqual(
            UserAddress._meta.get_field("phone").verbose_name, "Phone Number"
        )
        self.assertEqual(
            UserAddress._meta.get_field("mobile_phone").verbose_name,
            "Mobile Phone Number",
        )
        self.assertEqual(UserAddress._meta.get_field("notes").verbose_name, "Notes")
        self.assertEqual(UserAddress._meta.get_field("is_main").verbose_name, "Is Main")

    def test_meta_verbose_names(self):
        # Test verbose names for model
        self.assertEqual(UserAddress._meta.verbose_name, "User Address")
        self.assertEqual(UserAddress._meta.verbose_name_plural, "User Addresses")

    def test_str_representation(self):
        # Test the __str__ method returns the address title
        self.assertEqual(str(self.address), self.address.title)

    def test_get_user_addresses(self):
        address_1 = UserAddress.objects.create(
            user=self.user,
            title="Address 1",
            first_name="John",
            last_name="Doe",
            street="123 Main St",
            street_number="Apt 4B",
            city="Cityville",
            zipcode="12345",
            country=None,  # You can set an actual country object here
            region=None,  # You can set an actual region object here
            floor=FloorChoicesEnum.FIRST_FLOOR.value,
            location_type=LocationChoicesEnum.HOME.value,
            phone="123-456-7890",
            mobile_phone="987-654-3210",
            notes="Sample notes",
            is_main=False,
        )

        address_2 = UserAddress.objects.create(
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
        addresses = UserAddress.get_user_addresses(self.user)

        self.assertEqual(len(addresses), 3)
        self.assertIn(address_1, addresses)
        self.assertIn(address_2, addresses)

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

        # Ensure address is main is True and non_main_address_1 and non_main_address_2 are False
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
