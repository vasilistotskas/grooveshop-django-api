from typing import Callable
from typing import override

import factory
from django.core.exceptions import ValidationError
from django.db.models import Model
from faker import Faker

from core.logging import LogInfo

fake = Faker()


class UniqueFieldMixin:
    @classmethod
    def generate_unique_field(
        cls, model: type[Model], field_name: str, generator_func: Callable[[], any], max_attempts=20
    ) -> any:
        attempts = 0
        while attempts < max_attempts:
            value = generator_func()
            if not model.objects.filter(**{field_name: value}).exists():
                return value
            attempts += 1
        LogInfo.error(f"Failed to generate unique '{field_name}' for {model.__name__} after {max_attempts} attempts.")
        raise ValidationError(
            f"Unable to generate unique value for {field_name} on {model.__name__} after {max_attempts} attempts."
        )

    @classmethod
    def get_unique_value(cls, model: type[Model], field_name: str, generator_func: Callable[[], any]) -> any:
        return cls.generate_unique_field(model, field_name, generator_func)


class CustomDjangoModelFactory(factory.django.DjangoModelFactory):
    unique_model_fields = []

    class Meta:
        abstract = True
        exclude = ("unique_model_fields",)

    @classmethod
    @override
    def _create(cls, model_class: type[Model], *args, **kwargs):
        if hasattr(cls, "unique_model_fields"):
            for field, generator_func in cls.unique_model_fields:
                if field not in kwargs:
                    kwargs[field] = UniqueFieldMixin.get_unique_value(model_class, field, generator_func)
        return super()._create(model_class, *args, **kwargs)
