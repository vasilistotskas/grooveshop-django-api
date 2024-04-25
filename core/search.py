from django.apps import apps
from django.conf import settings
from django.db.models import Q
from django.db.models import Value

from core.postgres import FlatConcatSearchVector
from core.postgres import NoValidationSearchVector
from core.utils.html import preprocess_text

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]


def get_postgres_search_config(language_code: str) -> str:
    language_configs = settings.PARLER_LANGUAGES.get(settings.SITE_ID, ())
    for lang_config in language_configs:
        if lang_config.get("code") == language_code:
            return lang_config.get("name", "").lower()
    return "simple"


def prepare_translation_search_vector_value(
    model_instance,
    language_code: str,
    fields_weights: list[tuple[str, str]],
    config: str = "simple",
) -> FlatConcatSearchVector:
    translation = model_instance.translations.get(language_code=language_code)
    search_vectors = []
    for field, weight in fields_weights:
        raw_content = getattr(translation, field, "")
        cleaned_content = preprocess_text(raw_content)
        search_vector = NoValidationSearchVector(
            Value(cleaned_content),
            config=config,
            weight=weight,
        )
        search_vectors.append(search_vector)
    return FlatConcatSearchVector(*search_vectors)


def prepare_translation_search_document(
    model_instance, language_code: str, fields: list[str]
) -> str:
    translation = model_instance.translations.get(language_code=language_code)
    document_parts = []
    for field in fields:
        content = getattr(translation, field, None)
        if content is not None:
            cleaned_content = preprocess_text(content)
            document_parts.append(cleaned_content)
    return " ".join(document_parts)


def update_translation_search_vectors(
    model, app_label: str, fields_weights: list[tuple[str, str]]
) -> int:
    translation_model = apps.get_model(app_label, f"{model.__name__}Translation")
    active_languages = languages
    updated_count = 0
    for language_code in active_languages:
        translations = translation_model.objects.filter(
            Q(language_code=language_code)
            & (
                Q(search_vector_dirty=True)
                | Q(search_vector=None)
                | Q(search_vector="")
            ),
        )
        config = get_postgres_search_config(language_code)
        for translation in translations.iterator():
            translation.search_vector = prepare_translation_search_vector_value(
                translation.master, language_code, fields_weights, config
            )
            translation.search_vector_dirty = False
            translation.save(update_fields=["search_vector", "search_vector_dirty"])
            updated_count += 1

    return updated_count


def update_translation_search_documents(
    model, app_label: str, fields: list[str]
) -> int:
    translation_model = apps.get_model(app_label, f"{model.__name__}Translation")
    active_languages = languages
    updated_count = 0
    for language_code in active_languages:
        translations = translation_model.objects.filter(
            Q(language_code=language_code)
            & (
                Q(search_document_dirty=True)
                | Q(search_document=None)
                | Q(search_document="")
            ),
        )
        for translation in translations.iterator():
            translation.search_document = prepare_translation_search_document(
                translation.master, language_code, fields
            )
            translation.search_document_dirty = False
            translation.save(update_fields=["search_document", "search_document_dirty"])
            updated_count += 1

    return updated_count
