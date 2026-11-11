import factory

from product.models.brand import Brand


class BrandFactory(factory.django.DjangoModelFactory):
    # ``Brand.name`` is unique and the factory gets-or-creates on it, so a
    # random name that repeats hands back the earlier row: a loop of N
    # builds then adds fewer than N brands. A sequence cannot repeat.
    name = factory.Sequence(lambda n: f"Brand {n}")

    class Meta:
        model = Brand
        django_get_or_create = ("name",)
