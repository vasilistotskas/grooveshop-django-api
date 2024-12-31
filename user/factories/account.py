import factory
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password

from user.factories.address import UserAddressFactory

User = get_user_model()


class UserAccountFactory(factory.django.DjangoModelFactory):
    email = factory.Faker("email")
    plain_password = factory.Faker("password")
    password = factory.LazyAttribute(
        lambda o: make_password(
            o.plain_password if o.plain_password else "password"
        )
    )
    first_name = factory.Faker("first_name")
    last_name = factory.Faker("last_name")
    phone = factory.Faker("phone_number")
    city = factory.Faker("city")
    zipcode = factory.Faker("postcode")
    address = factory.Faker("address")
    place = factory.Faker("address")
    image = factory.django.ImageField(
        filename="user_image.jpg",
        color=factory.Faker("color"),
        width=800,
        height=800,
    )
    birth_date = factory.Faker("date_of_birth")
    twitter = factory.Faker("url")
    linkedin = factory.Faker("url")
    facebook = factory.Faker("url")
    instagram = factory.Faker("url")
    website = factory.Faker("url")
    youtube = factory.Faker("url")
    github = factory.Faker("url")
    bio = factory.Faker("paragraph")
    is_staff = False
    is_superuser = False

    class Meta:
        model = User
        django_get_or_create = ("email",)
        skip_postgeneration_save = True
        exclude = ("plain_password",)

    class Params:
        admin = factory.Trait(is_superuser=True, is_staff=True)
        staff = factory.Trait(is_staff=True)

    @factory.post_generation
    def num_addresses(self, create, extracted, **kwargs):
        if not create:
            return

        if extracted:
            UserAddressFactory.create_batch(extracted, user=self)
