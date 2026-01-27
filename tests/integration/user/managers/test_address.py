import pytest
from django.contrib.auth import get_user_model

from country.factories import CountryFactory
from region.factories import RegionFactory
from user.factories.account import UserAccountFactory
from user.factories.address import UserAddressFactory
from user.models.address import UserAddress

User = get_user_model()


@pytest.mark.django_db
class TestUserAddressManager:
    def setup_method(self):
        self.user = UserAccountFactory()
        self.other_user = UserAccountFactory()

    def test_get_user_addresses_empty(self):
        addresses = UserAddress.objects.get_user_addresses(self.user)

        assert addresses.count() == 0
        assert list(addresses) == []

    def test_get_user_addresses_single(self):
        address = UserAddressFactory(user=self.user, title="Home")

        addresses = UserAddress.objects.get_user_addresses(self.user)

        assert addresses.count() == 1
        assert address in addresses

    def test_get_user_addresses_multiple(self):
        address1 = UserAddressFactory(
            user=self.user, title="Home", is_main=True
        )
        address2 = UserAddressFactory(
            user=self.user, title="Work", is_main=False
        )
        address3 = UserAddressFactory(
            user=self.user, title="Secondary", is_main=False
        )

        UserAddressFactory(user=self.other_user, title="Other Home")

        addresses = UserAddress.objects.get_user_addresses(self.user)

        assert addresses.count() == 3
        assert address1 in addresses
        assert address2 in addresses
        assert address3 in addresses

    def test_get_user_addresses_ordering(self):
        work_address = UserAddressFactory(
            user=self.user, title="Work", is_main=False
        )
        home_address = UserAddressFactory(
            user=self.user, title="Home", is_main=True
        )
        secondary_address = UserAddressFactory(
            user=self.user, title="Secondary", is_main=False
        )

        addresses = list(UserAddress.objects.get_user_addresses(self.user))

        assert len(addresses) == 3
        assert addresses[0] == home_address
        assert addresses[1] == secondary_address
        assert addresses[2] == work_address

    def test_get_main_address_exists(self):
        regular_address = UserAddressFactory(
            user=self.user, title="Work", is_main=False
        )
        main_address = UserAddressFactory(
            user=self.user, title="Home", is_main=True
        )

        result = UserAddress.objects.get_main_address(self.user)

        assert result == main_address
        assert result != regular_address

    def test_get_main_address_none_exists(self):
        UserAddressFactory(user=self.user, title="Work", is_main=False)
        UserAddressFactory(user=self.user, title="Home", is_main=False)

        result = UserAddress.objects.get_main_address(self.user)

        assert result is None

    def test_get_main_address_no_addresses(self):
        result = UserAddress.objects.get_main_address(self.user)

        assert result is None

    def test_get_main_address_user_isolation(self):
        _ = UserAddressFactory(
            user=self.other_user, title="Other Home", is_main=True
        )

        UserAddressFactory(user=self.user, title="Our Work", is_main=False)

        result = UserAddress.objects.get_main_address(self.user)

        assert result is None

    def test_manager_inheritance(self):
        assert isinstance(UserAddress.objects, UserAddress.objects.__class__)
        assert hasattr(UserAddress.objects, "get_user_addresses")
        assert hasattr(UserAddress.objects, "get_main_address")

    def test_get_user_addresses_with_related_data(self):
        country = CountryFactory()
        region = RegionFactory(country=country)

        address1 = UserAddressFactory(
            user=self.user,
            title="International",
            country=country,
            region=region,
            is_main=True,
        )
        address2 = UserAddressFactory(
            user=self.user,
            title="Local",
            country=None,
            region=None,
            is_main=False,
        )

        addresses = UserAddress.objects.get_user_addresses(self.user)

        assert addresses.count() == 2
        assert address1 in addresses
        assert address2 in addresses

        ordered_addresses = list(addresses)
        assert ordered_addresses[0] == address1
        assert ordered_addresses[1] == address2

    def test_main_address_uniqueness_enforcement(self):
        main1 = UserAddressFactory(
            user=self.user, title="First Main", is_main=True
        )

        main2 = UserAddressFactory(
            user=self.user, title="Second Main", is_main=True
        )

        main1.refresh_from_db()

        assert not main1.is_main
        assert main2.is_main

        current_main = UserAddress.objects.get_main_address(self.user)
        assert current_main == main2

    def test_manager_with_different_users(self):
        user1_home = UserAddressFactory(
            user=self.user, title="User1 Home", is_main=True
        )
        user1_work = UserAddressFactory(
            user=self.user, title="User1 Work", is_main=False
        )

        user2_home = UserAddressFactory(
            user=self.other_user, title="User2 Home", is_main=True
        )
        user2_work = UserAddressFactory(
            user=self.other_user, title="User2 Work", is_main=False
        )

        user1_addresses = UserAddress.objects.get_user_addresses(self.user)
        user1_main = UserAddress.objects.get_main_address(self.user)

        assert user1_addresses.count() == 2
        assert user1_home in user1_addresses
        assert user1_work in user1_addresses
        assert user1_main == user1_home

        user2_addresses = UserAddress.objects.get_user_addresses(
            self.other_user
        )
        user2_main = UserAddress.objects.get_main_address(self.other_user)

        assert user2_addresses.count() == 2
        assert user2_home in user2_addresses
        assert user2_work in user2_addresses
        assert user2_main == user2_home

    def test_queryset_methods_are_chainable(self):
        UserAddressFactory(
            user=self.user, title="Home", city="New York", is_main=True
        )
        UserAddressFactory(
            user=self.user, title="Work", city="Boston", is_main=False
        )

        ny_addresses = UserAddress.objects.get_user_addresses(self.user).filter(
            city="New York"
        )
        assert ny_addresses.count() == 1

        addresses_by_city = UserAddress.objects.get_user_addresses(
            self.user
        ).order_by("city")
        cities = [addr.city for addr in addresses_by_city]
        assert cities == ["Boston", "New York"]
