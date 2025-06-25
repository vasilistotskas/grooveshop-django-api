import logging
from collections.abc import Callable

import factory
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Model
from faker import Faker

logger = logging.getLogger(__name__)

fake = Faker()


class TranslationUtilities:
    """Utilities for handling django-parler translations in factories"""

    @staticmethod
    def get_available_languages():
        """Get available languages from django-parler settings"""
        try:
            return [
                lang["code"]
                for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
            ]
        except (AttributeError, KeyError):
            logger.warning(
                "PARLER_LANGUAGES not properly configured, using default language"
            )
            return [settings.LANGUAGE_CODE]

    @staticmethod
    def is_translation_factory(factory_class):
        """Check if a factory is for a translation model"""
        if not hasattr(factory_class, "_meta") or not hasattr(
            factory_class._meta, "model"
        ):
            return False

        model_name = factory_class._meta.model.__name__
        return model_name.endswith("Translation")

    @staticmethod
    def get_master_factory_from_translation(factory_class):
        """Get the master factory from a translation factory"""
        if not TranslationUtilities.is_translation_factory(factory_class):
            return None

        # Look for master field in factory
        if hasattr(factory_class, "master"):
            master_field = factory_class.master
            if hasattr(master_field, "_factory"):
                return master_field._factory

        return None

    @staticmethod
    def has_translations(factory_class):
        """Check if a factory creates translations via post_generation"""
        if not hasattr(factory_class, "translations"):
            return False

        # Check if it's a post_generation method
        translations_attr = factory_class.translations
        return (
            hasattr(translations_attr, "_decorator")
            and translations_attr._decorator == "post_generation"
        )

    @staticmethod
    def validate_translation_completeness(instance, model_class):
        """Validate that all required translations exist for an instance"""
        if not hasattr(model_class, "_parler_meta"):
            return True  # Not a translatable model

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
                f"Missing translations for {model_class.__name__} instance {instance.pk}: {missing_languages}"
            )
            return False

        return True


class UniqueFieldMixin:
    @classmethod
    def generate_unique_field(
        cls,
        model: type[Model],
        field_name: str,
        generator_func: Callable[[], any],
        max_attempts=20,
    ):
        attempts = 0
        while attempts < max_attempts:
            value = generator_func()
            if not model.objects.filter(**{field_name: value}).exists():
                return value
            attempts += 1
        logger.error(
            f"Failed to generate unique '{field_name}' for {model.__name__} after {max_attempts} attempts."
        )
        raise ValidationError(
            f"Unable to generate unique value for {field_name} on {model.__name__} after {max_attempts} attempts."
        )

    @classmethod
    def get_unique_value(
        cls,
        model: type[Model],
        field_name: str,
        generator_func: Callable[[], any],
    ):
        return cls.generate_unique_field(model, field_name, generator_func)


class CustomDjangoModelFactory(factory.django.DjangoModelFactory):
    """Enhanced base factory with translation support, and dependency management."""

    depends_on = []  # Explicit factory dependencies
    conditional_depends_on = {}  # Conditional dependencies: {'condition': ['FactoryName']}
    execution_priority = 0  # Higher number = executed later

    locale_aware = True  # Enable locale-specific data
    relationship_aware = False  # Enable relationship intelligence

    class Meta:
        abstract = True
        exclude = (
            "depends_on",
            "conditional_depends_on",
            "execution_priority",
            "locale_aware",
            "relationship_aware",
            "unique_model_fields",
        )

    @classmethod
    def _create(cls, model_class: type[Model], *args, **kwargs):
        # Handle unique fields
        if hasattr(cls, "unique_model_fields"):
            for field, generator_func in cls.unique_model_fields:
                if field not in kwargs:
                    kwargs[field] = UniqueFieldMixin.get_unique_value(
                        model_class, field, generator_func
                    )

        # Create the instance first
        instance = super()._create(model_class, *args, **kwargs)

        # This is now handled by the command, not here

        return instance

    @classmethod
    def create_with_translation_validation(cls, **kwargs):
        """Create an instance and validate translation completeness"""
        instance = cls.create(**kwargs)

        # Validate translations if this is a translatable model
        if TranslationUtilities.validate_translation_completeness(
            instance, cls._meta.model
        ):
            logger.debug(
                f"Successfully created {cls._meta.model.__name__} with complete translations"
            )

        return instance
