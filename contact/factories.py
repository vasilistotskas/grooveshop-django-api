import factory

from contact import signals
from contact.models import Contact


@factory.django.mute_signals(signals.post_save)
class ContactFactory(factory.django.DjangoModelFactory):
    name = factory.Faker("name")
    email = factory.Faker("email")
    message = factory.Faker("paragraph")

    class Meta:
        model = Contact
        django_get_or_create = ("email",)
