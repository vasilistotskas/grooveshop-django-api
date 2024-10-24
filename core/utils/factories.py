import importlib
from typing import override

from factory import declarations
from factory.faker import Faker


class MaxLengthFaker(declarations.BaseDeclaration):
    def __init__(self, provider, model_class, field_name, **kwargs):
        super().__init__(**kwargs)
        self.provider = provider
        self.model_class = model_class
        self.field_name = field_name
        self.provider_kwargs = kwargs

    @override
    def evaluate(self, instance, step, extra):
        faker = Faker._get_faker()  # noqa
        value = getattr(faker, self.provider)(**self.provider_kwargs)
        max_length = self.model_class._meta.get_field(self.field_name).max_length  # noqa
        if max_length:
            return value[:max_length]
        return value


def generate_unique_country_codes():
    from faker import Faker

    fake = Faker()

    country_model = importlib.import_module("country.models").Country
    existing_alpha_2_codes = set(country_model.objects.values_list("alpha_2", flat=True))
    existing_alpha_3_codes = set(country_model.objects.values_list("alpha_3", flat=True))

    while True:
        alpha_2_code = fake.country_code(representation="alpha-2")
        alpha_3_code = fake.country_code(representation="alpha-3")
        if alpha_2_code not in existing_alpha_2_codes and alpha_3_code not in existing_alpha_3_codes:
            return alpha_2_code, alpha_3_code
