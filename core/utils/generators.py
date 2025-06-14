import random
import string
from collections.abc import Callable
from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.db.models import Model
from django.utils.text import slugify


def default_random_string_generator(size: int = 5) -> str:
    chars = string.ascii_lowercase + string.digits
    return "".join(random.choices(chars, k=size))


@dataclass
class SlugifyConfig:
    instance: Model
    slug_field: str = "slug"
    title_field: str = "title"
    invalid_slug: str = "create"
    size: int = 5
    max_attempts: int = 10
    random_string_generator: Callable[[int], str] = field(
        default_factory=lambda: default_random_string_generator
    )


def unique_slugify(config: SlugifyConfig) -> str:
    base_slug = slugify(getattr(config.instance, config.title_field, ""))
    if base_slug == config.invalid_slug or not base_slug:
        base_slug = f"{config.invalid_slug}-{config.random_string_generator(config.size)}"

    ModelClass: type[Model] = type(config.instance)
    slug = base_slug
    attempt = 0

    while attempt < config.max_attempts:
        lookup = {f"{config.slug_field}__iexact": slug}
        if not ModelClass._default_manager.filter(**lookup).exists():
            setattr(config.instance, config.slug_field, slug)
            return slug

        slug = f"{base_slug}-{config.random_string_generator(config.size)}"
        attempt += 1

    raise ValidationError(
        f"Unable to generate a unique slug for {ModelClass.__name__} after {config.max_attempts} attempts."
    )
