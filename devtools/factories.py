import logging
from collections.abc import Callable
from dataclasses import dataclass
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


@dataclass
class SeedingResult:
    """Result of custom seeding operation"""

    created_count: int
    skipped_count: int = 0
    errors: list[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []

    @property
    def total_processed(self) -> int:
        return self.created_count + self.skipped_count


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

        translation_model = model_class._parler_meta.root_model

        for language_code in available_languages:
            translation_fields = (
                translation_data.get(language_code, {})
                if translation_data
                else {}
            )

            try:
                translation, created = translation_model.objects.get_or_create(
                    language_code=language_code,
                    master=instance,
                    defaults=translation_fields,
                )

                if not created and translation_fields:
                    updated = False
                    for field, value in translation_fields.items():
                        if hasattr(translation, field) and value is not None:
                            current_value = getattr(translation, field, None)
                            if current_value != value:
                                setattr(translation, field, value)
                                updated = True

                    if updated:
                        try:
                            translation.save()
                            logger.debug(
                                f"Updated existing {language_code} translation for "
                                f"{model_class.__name__} instance {instance.pk}"
                            )
                        except Exception as e:
                            logger.warning(
                                f"Failed to update {language_code} translation: {e}"
                            )

                if created:
                    logger.debug(
                        f"Created {language_code} translation for "
                        f"{model_class.__name__} instance {instance.pk}"
                    )

            except Exception as e:
                logger.warning(
                    f"Failed to ensure {language_code} translation for "
                    f"{model_class.__name__} instance {instance.pk}: {e}. "
                    f"Continuing to next language."
                )
                continue


class SeedingStrategyRegistry:
    """
    Registry for custom seeding strategies.
    Allows runtime registration of seeding behaviors.
    """

    _strategies: dict[str, Callable] = {}

    @classmethod
    def register(cls, factory_name: str, strategy: Callable):
        """Register a custom seeding strategy for a factory."""
        cls._strategies[factory_name] = strategy

    @classmethod
    def get_strategy(cls, factory_name: str) -> Callable | None:
        """Get the registered strategy for a factory."""
        return cls._strategies.get(factory_name)

    @classmethod
    def has_strategy(cls, factory_name: str) -> bool:
        """Check if a factory has a registered strategy."""
        return factory_name in cls._strategies

    @classmethod
    def clear(cls):
        """Clear all registered strategies."""
        cls._strategies.clear()


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
        generator_name: str | None = None,
        _retry_count: int = 0,
    ) -> Any:
        """
        Generate a unique value for a field with retries.

        Args:
            model: The model class
            field_name: Name of the field
            generator_func: Function to generate values
            max_attempts: Maximum number of attempts
            error_callback: Optional callback called on each failed attempt
            generator_name: Optional human-readable name for the generator function
            _retry_count: Internal retry counter (for logging control)

        Returns:
            Unique value for the field

        Raises:
            UniqueFieldError: If unable to generate unique value
        """
        attempts = 0
        generated_values = set()
        duplicate_generations = 0
        existing_db_conflicts = 0

        while attempts < max_attempts:
            value = generator_func()

            if value in generated_values:
                duplicate_generations += 1
                attempts += 1
                continue

            generated_values.add(value)

            if not model.objects.filter(**{field_name: value}).exists():
                return value

            existing_db_conflicts += 1
            attempts += 1

            if error_callback:
                error_callback(attempts)

        failure_reasons = []

        if duplicate_generations > 0:
            failure_reasons.append(
                f"generator produced {duplicate_generations} duplicate values"
            )

        if existing_db_conflicts > 0:
            failure_reasons.append(
                f"{existing_db_conflicts} values already exist in database"
            )

        existing_count = model.objects.count()

        func_name = generator_name or getattr(
            generator_func, "__name__", "unknown"
        )
        if func_name == "<lambda>":
            func_name = f"lambda function (module: {getattr(generator_func, '__module__', 'unknown')})"

        if _retry_count == 0:
            generated_values_display = list(generated_values)[:10]
            if len(generated_values) > 10:
                generated_values_display.append("...")

            logger.error(
                f"❌ Unique field generation failed for {model.__name__}.{field_name}\n"
                f"   📊 Attempts: {max_attempts} | Generated: {len(generated_values)} unique values | DB records: {existing_count}\n"
                f"   🔧 Generator: {func_name}\n"
                f"   📋 Values tried: {generated_values_display}\n"
                f"   ⚠️  Issues: {'; '.join(failure_reasons) if failure_reasons else 'unknown'}\n"
                f"   📈 Stats: {duplicate_generations} duplicates, {existing_db_conflicts} DB conflicts"
            )

            suggestions = []

            if len(generated_values) <= 5:
                suggestions.append(
                    f"🔍 Limited generator variation ({len(generated_values)} unique values)"
                )
                suggestions.append(
                    "💡 Use a more diverse generator (e.g., random strings, UUIDs, or larger value pools)"
                )

            if existing_count > max_attempts:
                suggestions.append(
                    f"📈 High DB record count ({existing_count}) vs attempts ({max_attempts})"
                )
                suggestions.append(
                    "💡 Consider increasing max_attempts or using sequential/incremental generators"
                )

            if (
                existing_count >= len(generated_values)
                and len(generated_values) <= 10
            ):
                suggestions.append(
                    "🎯 Generator pool exhausted - all possible values may be taken"
                )
                suggestions.append(
                    "💡 Expand the generator's value pool or use a different generation strategy"
                )

            if suggestions:
                logger.warning(
                    "🔧 Suggestions to fix this issue:\n   "
                    + "\n   ".join(suggestions)
                )
        else:
            logger.warning(
                f"🔄 Retry {_retry_count} failed for {model.__name__}.{field_name}: "
                f"{failure_reasons[0] if failure_reasons else 'generation failed'}"
            )

        primary_issue = (
            failure_reasons[0] if failure_reasons else "generator exhaustion"
        )
        raise UniqueFieldError(
            f"Unique {field_name} generation failed: {primary_issue} "
            f"({len(generated_values)} unique values from {max_attempts} attempts)"
        )

    @classmethod
    def get_unique_value(
        cls,
        model: type[M],
        field_name: str,
        generator_func: Callable[[], Any],
        generator_name: str | None = None,
        **kwargs,
    ) -> Any:
        """Convenience method for generating unique values"""
        return cls.generate_unique_field(
            model,
            field_name,
            generator_func,
            generator_name=generator_name,
            **kwargs,
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
    execution_priority: int = 0

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
        pass

    @classmethod
    def _get_translation_data(
        cls, context: dict[str, Any]
    ) -> dict[str, dict[str, Any]]:
        """Get translation data for all languages"""
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


def custom_seeding(
    description: str = None,
    settings_key: str = None,
    dependencies: list[str] = None,
):
    """
    Decorator to mark a factory as using custom seeding.

    This avoids inheritance issues by adding attributes directly.

    Usage:
        @custom_seeding(
            description="Seeds based on language settings",
            settings_key="PARLER_LANGUAGES",
            dependencies=["Country"]
        )
        class MyFactory(CustomDjangoModelFactory):
            @classmethod
            def custom_seed(cls, **kwargs):
                ...
    """

    def decorator(factory_class):
        factory_class.use_custom_seeding = True

        if description:
            factory_class._seeding_description = description

        if settings_key:
            factory_class._settings_key = settings_key

        if dependencies:
            factory_class._dependencies = dependencies

        if not hasattr(factory_class, "get_seeding_description"):
            factory_class.get_seeding_description = classmethod(
                lambda cls: getattr(
                    cls,
                    "_seeding_description",
                    f"Custom seeding for {cls.__name__}",
                )
            )

        if not hasattr(factory_class, "get_settings_key"):
            factory_class.get_settings_key = classmethod(
                lambda cls: getattr(cls, "_settings_key", None)
            )

        if not hasattr(factory_class, "get_dependencies"):
            factory_class.get_dependencies = classmethod(
                lambda cls: getattr(cls, "_dependencies", [])
            )

        if not hasattr(factory_class, "custom_seed"):
            raise TypeError(
                f"{factory_class.__name__} must implement custom_seed() method"
            )

        return factory_class

    return decorator
