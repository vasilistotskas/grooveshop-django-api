import factory

from product.models.brand import Brand


class BrandFactory(factory.django.DjangoModelFactory):
    name = factory.Faker("company")

    class Meta:
        model = Brand
        django_get_or_create = ("name",)
