import factory

from user.enum.address import FloorChoicesEnum
from user.enum.address import LocationChoicesEnum
from user.models.address import UserAddress


class UserAddressFactory(factory.django.DjangoModelFactory):
    user = factory.SubFactory("user.factories.account.UserAccountFactory")
    title = factory.Faker("word")
    first_name = factory.Faker("first_name")
    last_name = factory.Faker("last_name")
    street = factory.Faker("street_name")
    street_number = factory.Faker("building_number")
    city = factory.Faker("city")
    zipcode = factory.Faker("postcode")
    country = factory.SubFactory("country.factories.CountryFactory")
    region = factory.SubFactory("region.factories.RegionFactory")
    floor = factory.Iterator([choice.value for choice in FloorChoicesEnum])
    location_type = factory.Iterator([choice.value for choice in LocationChoicesEnum])
    phone = factory.Faker("phone_number")
    mobile_phone = factory.Faker("phone_number")
    notes = factory.Faker("sentence")
    is_main = factory.Faker("boolean")

    class Meta:
        model = UserAddress
        django_get_or_create = ("user", "title")
