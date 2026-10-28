import factory

from texts.models import TextOverride, TextSurface


class TextOverrideFactory(factory.django.DjangoModelFactory):
    surface = TextSurface.STOREFRONT
    namespace = "components/Chrome/Footer"
    key = factory.Sequence(lambda n: f"label_{n}")
    language = "en"
    value = factory.Faker("sentence")

    class Meta:
        model = TextOverride
