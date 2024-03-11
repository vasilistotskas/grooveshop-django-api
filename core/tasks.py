from __future__ import absolute_import
from __future__ import unicode_literals

import logging

from celery import shared_task
from django.apps import apps
from django.conf import settings
from django.core import management
from django.db.models import Q
from django.db.models import Value

from app import celery_app
from core.postgres import FlatConcatSearchVector
from core.postgres import NoValidationSearchVector
from product.models.product import Product

logger = logging.getLogger("celery")
languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]


@shared_task(bind=True, name="Clear Expired Sessions Task")
def clear_expired_sessions_task():
    try:
        management.call_command("clearsessions", verbosity=0)
        return "All expired sessions deleted."
    except Exception as e:
        return f"error: {e}"


@shared_task(bind=True, name="Clear All Cache Task")
def clear_all_cache_task():
    try:
        management.call_command("clear_cache", verbosity=0)
        return "All cache deleted."
    except Exception as e:
        return f"error: {e}"


@shared_task(bind=True, name="Clear Carts For None Users Task")
def clear_carts_for_none_users_task():
    from cart.models import Cart

    null_carts = Cart.objects.filter(user=None)
    null_carts.delete()

    message = f"Cleared {len(null_carts)} null carts."

    logger.info(message)
    return message


@shared_task(bind=True, name="Cleanup Log Files Task")
def cleanup_log_files_task(self, days=30):
    from django.conf import settings
    from os import path, remove, listdir
    from datetime import datetime, timedelta

    logs_path = path.join(settings.BASE_DIR, "logs")
    files = listdir(logs_path)
    now = datetime.now()

    for file in files:
        file_path = path.join(logs_path, file)
        file_modification_date = datetime.fromtimestamp(path.getmtime(file_path))
        if now - file_modification_date > timedelta(days=days):
            remove(file_path)

    message = f"Removed log files older than {days} days."

    logger.info(message)
    return message


@shared_task(bind=True, name="Clear Blacklisted expired tokens Task")
def clear_blacklisted_tokens_task():
    try:
        management.call_command("flushexpiredtokens", verbosity=0)
        return "All expired blacklisted tokens deleted."
    except Exception as e:
        return f"error: {e}"


BATCH_SIZE = 500
PRODUCTS_BATCH_SIZE = 300
PRODUCT_FIELDS_TO_PREFETCH = [
    "translations",
]


def prepare_product_translation_search_vector_value(
    product: "Product", language_code: str, config="simple"
) -> FlatConcatSearchVector:
    translation = product.translations.get(language_code=language_code)
    search_vectors = [
        NoValidationSearchVector(Value(translation.name), config=config, weight="A"),
        NoValidationSearchVector(
            Value(translation.description), config=config, weight="C"
        ),
        NoValidationSearchVector(Value(product.slug), config="simple", weight="B"),
    ]

    return FlatConcatSearchVector(*search_vectors)


def prepare_product_translation_search_document(
    product: "Product", language_code: str
) -> str:
    translation = product.translations.get(language_code=language_code)
    document_parts = [product.slug, translation.name, translation.description]
    return " ".join(filter(None, document_parts))


@celery_app.task
def update_product_translation_search_vectors():
    ProductTranslation = apps.get_model("product", "ProductTranslation")
    active_languages = languages
    updated_count = 0

    for language_code in active_languages:
        translations = ProductTranslation.objects.filter(
            Q(language_code=language_code)
            & (Q(search_vector_dirty=True) | Q(search_vector=None)),
        )

        for translation in translations.iterator():
            translation.search_vector = prepare_product_translation_search_vector_value(
                translation.master, language_code
            )
            translation.search_vector_dirty = False
            translation.save(update_fields=["search_vector", "search_vector_dirty"])
            updated_count += 1

    logger.info(f"Updated search vectors for {updated_count} product translations.")


@celery_app.task
def update_product_translation_search_documents():
    ProductTranslation = apps.get_model("product", "ProductTranslation")
    active_languages = languages
    updated_count = 0

    for language_code in active_languages:
        translations = ProductTranslation.objects.filter(
            Q(language_code=language_code)
            & (Q(search_document_dirty=True) | Q(search_document="")),
        )

        for translation in translations.iterator():
            translation.search_document = prepare_product_translation_search_document(
                translation.master, language_code
            )
            translation.search_document_dirty = False
            translation.save(update_fields=["search_document", "search_document_dirty"])
            updated_count += 1

    logger.info(f"Updated search documents for {updated_count} product translations.")
