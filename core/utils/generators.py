import random
import string
from dataclasses import dataclass
from dataclasses import field
from typing import Callable

from django.core.exceptions import ValidationError
from django.db.models import Model
from django.utils.text import slugify


@dataclass
class SlugifyConfig:
    instance: Model
    slug_field: str = "slug"
    title_field: str = "title"
    invalid_slug: str = "create"
    size: int = 5
    max_attempts: int = 10
    random_string_generator: Callable[[int], str] = field(default=None)

    def __post_init__(self):
        if not self.random_string_generator:
            self.random_string_generator = self.default_random_string_generator

    @staticmethod
    def default_random_string_generator(size=5) -> str:
        chars = string.ascii_lowercase + string.digits
        return "".join(random.choices(chars, k=size))


def unique_slugify(config: SlugifyConfig) -> str:
    base_slug = slugify(getattr(config.instance, config.title_field, ""))
    if base_slug == config.invalid_slug or not base_slug:
        base_slug = (
            f"{config.invalid_slug}-{config.random_string_generator(config.size)}"
        )

    ModelClass = type(config.instance)
    slug = base_slug
    attempt = 0

    while attempt < config.max_attempts:
        lookup = {f"{config.slug_field}__iexact": slug}
        if not ModelClass.objects.filter(**lookup).exists():
            setattr(config.instance, config.slug_field, slug)  # Set slug on instance
            return slug  # Unique slug found

        slug = f"{base_slug}-{config.random_string_generator(config.size)}"
        attempt += 1

    raise ValidationError(
        f"Unable to generate a unique slug for {ModelClass.__name__} after {config.max_attempts} attempts."
    )
