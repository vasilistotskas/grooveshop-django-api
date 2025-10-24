import factory

from vat.models import Vat


class VatFactory(factory.django.DjangoModelFactory):
    value = factory.Faker(
        "random_element",
        elements=[0, 5, 6, 7, 8, 10, 13, 15, 19, 20, 21, 23, 24, 25, 27],
    )

    class Meta:
        model = Vat
        django_get_or_create = ("value",)
