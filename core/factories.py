import logging
from collections.abc import Callable
from typing import Any, TypeVar

import factory
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Model
from faker import Faker

logger = logging.getLogger(__name__)

fake = Faker()

M = TypeVar("M", bound=Model)


class TranslationError(Exception):
    """Raised when translation operations fail"""

    pass


class UniqueFieldError(ValidationError):
    """Raised when unique field generation fails"""

    pass


class TranslationUtilities:
    """Utilities for handling django-parler translations in factories"""

    _language_cache: list[str] | None = None

    @classmethod
    def get_available_languages(cls) -> list[str]:
        """Get available languages from django-parler settings with caching"""
        if cls._language_cache is None:
            try:
                cls._language_cache = [
                    lang["code"]
                    for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
                ]
            except (AttributeError, KeyError) as e:
                logger.warning(
                    f"PARLER_LANGUAGES not properly configured: {e}. "
                    f"Using default language: {settings.LANGUAGE_CODE}"
                )
                cls._language_cache = [settings.LANGUAGE_CODE]

        return cls._language_cache

    @staticmethod
    def is_translation_factory(factory_class: type[factory.Factory]) -> bool:
        """Check if a factory is for a translation model"""
        if not hasattr(factory_class, "_meta") or not hasattr(
            factory_class._meta, "model"
        ):
            return False

        model_name = factory_class._meta.model.__name__
        return model_name.endswith("Translation")

    @staticmethod
    def get_master_factory_from_translation(
        factory_class: type[factory.Factory],
    ) -> type[factory.Factory] | None:
        """Get the master factory from a translation factory"""
        if not TranslationUtilities.is_translation_factory(factory_class):
            return None

        if hasattr(factory_class, "master"):
            master_field = factory_class.master
            if hasattr(master_field, "_factory"):
                return master_field._factory

        return None

    @staticmethod
    def has_translations(factory_class: type[factory.Factory]) -> bool:
        """Check if a factory creates translations via post_generation"""
        if not hasattr(factory_class, "translations"):
            return False

        translations_attr = factory_class.translations
        return (
            hasattr(translations_attr, "_decorator")
            and translations_attr._decorator == "post_generation"
        )

    @staticmethod
    def validate_translation_completeness(
        instance: Model, model_class: type[Model]
    ) -> bool:
        """Validate that all required translations exist for an instance"""
        if not hasattr(model_class, "_parler_meta"):
            return True

        available_languages = TranslationUtilities.get_available_languages()
        translation_model = model_class._parler_meta.root_model

        existing_languages = set(
            translation_model.objects.filter(master=instance).values_list(
                "language_code", flat=True
            )
        )

        missing_languages = set(available_languages) - existing_languages

        if missing_languages:
            logger.warning(
                f"Missing translations for {model_class.__name__} "
                f"instance {instance.pk}: {missing_languages}"
            )
            return False

        return True

    @classmethod
    def ensure_translations(
        cls,
        instance: Model,
        translation_data: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        """Ensure all required translations exist for an instance"""
        model_class = instance.__class__

        if not hasattr(model_class, "_parler_meta"):
            return

        available_languages = cls.get_available_languages()

        for language_code in available_languages:
            if not instance.has_translation(language_code):
                try:
                    translation_fields = (
                        translation_data.get(language_code, {})
                        if translation_data
                        else {}
                    )

                    instance.create_translation(
                        language_code=language_code, **translation_fields
                    )
                    logger.debug(
                        f"Created {language_code} translation for "
                        f"{model_class.__name__} instance {instance.pk}"
                    )
                except Exception as e:
                    raise TranslationError(
                        f"Failed to create {language_code} translation for "
                        f"{model_class.__name__}: {e}"
                    ) from e


class UniqueFieldMixin:
    """Mixin for handling unique field generation with improved error handling"""

    @classmethod
    def generate_unique_field(
        cls,
        model: type[M],
        field_name: str,
        generator_func: Callable[[], Any],
        max_attempts: int = 20,
        error_callback: Callable[[int], None] | None = None,
    ) -> Any:
        """
        Generate a unique value for a field with retries.

        Args:
            model: The model class
            field_name: Name of the field
            generator_func: Function to generate values
            max_attempts: Maximum number of attempts
            error_callback: Optional callback called on each failed attempt

        Returns:
            Unique value for the field

        Raises:
            UniqueFieldError: If unable to generate unique value
        """
        attempts = 0
        generated_values = set()

        while attempts < max_attempts:
            value = generator_func()

            if value in generated_values:
                attempts += 1
                continue

            generated_values.add(value)

            if not model.objects.filter(**{field_name: value}).exists():
                return value

            attempts += 1

            if error_callback:
                error_callback(attempts)

        logger.error(
            f"Failed to generate unique '{field_name}' for {model.__name__} "
            f"after {max_attempts} attempts. Tried values: {list(generated_values)[:10]}..."
        )

        raise UniqueFieldError(
            f"Unable to generate unique value for {field_name} on {model.__name__} "
            f"after {max_attempts} attempts."
        )

    @classmethod
    def get_unique_value(
        cls,
        model: type[M],
        field_name: str,
        generator_func: Callable[[], Any],
        **kwargs,
    ) -> Any:
        """Convenience method for generating unique values"""
        return cls.generate_unique_field(
            model, field_name, generator_func, **kwargs
        )


class LocaleAwareMixin:
    """Mixin for locale-aware data generation"""

    locale_config: dict[str, dict[str, Any]] = {}

    @classmethod
    def get_locale_config(
        cls, market: str, field: str
    ) -> dict[str, Any] | None:
        """Get locale-specific configuration for a field"""
        market_config = cls.locale_config.get(market, {})
        return market_config.get(field)

    @classmethod
    def generate_locale_aware_value(
        cls,
        field: str,
        default_generator: Callable[[], Any],
        market: str = "global",
    ) -> Any:
        """Generate value with locale awareness"""
        locale_config = cls.get_locale_config(market, field)

        if locale_config and "generator" in locale_config:
            return locale_config["generator"]()

        return default_generator()


class CustomDjangoModelFactory(
    factory.django.DjangoModelFactory, UniqueFieldMixin, LocaleAwareMixin
):
    """Enhanced base factory with translation support, unique fields, and dependency management."""

    depends_on: list[str] = []
    conditional_depends_on: dict[str, list[str]] = {}
    execution_priority: int = 0  # Higher number = executed later

    locale_aware: bool = True
    relationship_aware: bool = False
    auto_translations: bool = True

    unique_model_fields: list[tuple[str, Callable[[], Any]]] = []

    class Meta:
        abstract = True
        exclude = (
            "depends_on",
            "conditional_depends_on",
            "execution_priority",
            "locale_aware",
            "relationship_aware",
            "unique_model_fields",
            "auto_translations",
        )

    @classmethod
    def _create(cls, model_class: type[M], *args, **kwargs) -> M:
        """Enhanced create method with unique fields and translation support"""
        context = kwargs.pop("_context", {})

        if hasattr(cls, "unique_model_fields"):
            for field, generator_func in cls.unique_model_fields:
                if field not in kwargs:
                    kwargs[field] = cls.get_unique_value(
                        model_class, field, generator_func
                    )

        if cls.locale_aware and context.get("market"):
            cls._apply_locale_aware_fields(kwargs, context["market"])

        with transaction.atomic():
            instance = super()._create(model_class, *args, **kwargs)

            if cls.auto_translations and hasattr(model_class, "_parler_meta"):
                try:
                    TranslationUtilities.ensure_translations(
                        instance, cls._get_translation_data(context)
                    )
                except TranslationError as e:
                    logger.error(f"Translation creation failed: {e}")
                    if not context.get("ignore_translation_errors", False):
                        raise

        return instance

    @classmethod
    def _apply_locale_aware_fields(
        cls, kwargs: dict[str, Any], market: str
    ) -> None:
        """Apply locale-aware field generation"""
        # This can be overridden in subclasses for specific locale handling
        pass

    @classmethod
    def _get_translation_data(
        cls, context: dict[str, Any]
    ) -> dict[str, dict[str, Any]]:
        """Get translation data for all languages"""
        # This can be overridden in subclasses
        return {}

    @classmethod
    def create_with_context(cls, **kwargs) -> M:
        """Create an instance with additional context"""
        context = kwargs.pop("context", {})
        kwargs["_context"] = context
        return cls.create(**kwargs)

    @classmethod
    def create_batch(cls, size: int, **kwargs) -> list[M]:
        """Create multiple instances efficiently"""
        instances = []

        with transaction.atomic():
            for _ in range(size):
                instances.append(cls.create(**kwargs))

        return instances

    @classmethod
    def create_with_translation_validation(cls, **kwargs) -> M:
        """Create an instance and validate translation completeness"""
        instance = cls.create(**kwargs)

        if TranslationUtilities.validate_translation_completeness(
            instance, cls._meta.model
        ):
            logger.debug(
                f"Successfully created {cls._meta.model.__name__} "
                f"with complete translations"
            )
        else:
            logger.warning(
                f"Created {cls._meta.model.__name__} instance {instance.pk} "
                f"with incomplete translations"
            )

        return instance
