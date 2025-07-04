import factory

from vat.models import Vat


class VatFactory(factory.django.DjangoModelFactory):
    value = factory.Faker("random_int", min=1, max=100)

    class Meta:
        model = Vat
        django_get_or_create = ("value",)
