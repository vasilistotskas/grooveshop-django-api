from __future__ import absolute_import
from __future__ import unicode_literals

import logging

from celery import shared_task
from django.conf import settings
from django.core import management
from django.db.models import prefetch_related_objects
from django.db.models import QuerySet
from django.db.models import Value
from django.utils.translation import override

from app import celery_app
from core.postgres import FlatConcatSearchVector
from core.postgres import NoValidationSearchVector
from product.models.product import Product

logger = logging.getLogger("celery")


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


def _prep_product_search_vector_index(products):
    prefetch_related_objects(products, *PRODUCT_FIELDS_TO_PREFETCH)
    for product in products:
        product.search_vector = FlatConcatSearchVector(
            *prepare_product_search_vector_value(product, already_prefetched=True)
        )
        product.search_index_dirty = False

    Product.objects.bulk_update(
        products, ["search_vector", "updated_at", "search_index_dirty"]
    )


def update_products_search_vector(products: "QuerySet", use_batches=True):
    if use_batches:
        last_id = 0
        while True:
            products_batch = list(products.filter(id__gt=last_id)[:PRODUCTS_BATCH_SIZE])
            if not products_batch:
                break
            last_id = products_batch[-1].id
            _prep_product_search_vector_index(products_batch)
    else:
        _prep_product_search_vector_index(products)


def prepare_product_search_vector_value(
    product: "Product", *, already_prefetched=False
) -> list[NoValidationSearchVector]:
    if not already_prefetched:
        prefetch_related_objects([product], *PRODUCT_FIELDS_TO_PREFETCH)

    search_vectors = []

    for language in product.get_available_languages():
        with override(language):
            product_trans = product.translations.get(language_code=language)

            search_vectors.extend(
                [
                    NoValidationSearchVector(
                        Value(product_trans.name), config="simple", weight="A"
                    ),
                    NoValidationSearchVector(
                        Value(product_trans.description), config="simple", weight="C"
                    ),
                ]
            )

    search_vectors.append(
        NoValidationSearchVector(Value(product.slug), config="simple", weight="B")
    )

    return search_vectors


def prepare_product_search_document(
    product: "Product", *, already_prefetched=False
) -> str:
    if not already_prefetched:
        prefetch_related_objects([product], *PRODUCT_FIELDS_TO_PREFETCH)

    document_parts = [product.slug]

    for language in product.get_available_languages():
        with override(language):
            product_trans = product.translations.get(language_code=language)
            if product_trans.name:
                document_parts.append(product_trans.name)
            if product_trans.description:
                document_parts.append(product_trans.description)

    search_document = " ".join(document_parts)

    return search_document


def set_search_document_values(
    instances: list, prepare_search_document_func: callable
) -> int:
    if not instances:
        return 0
    Model = instances[0]._meta.model
    for instance in instances:
        instance.search_document = prepare_search_document_func(
            instance, already_prefetched=True
        )
    Model.objects.bulk_update(instances, ["search_document"])

    return len(instances)


def set_search_vector_values(
    instances: list,
    prepare_search_vector_func: callable,
) -> int:
    Model = instances[0]._meta.model
    for instance in instances:
        instance.search_vector = FlatConcatSearchVector(
            *prepare_search_vector_func(instance, already_prefetched=True)
        )
    Model.objects.bulk_update(instances, ["search_vector"])

    return len(instances)


@celery_app.task
def set_product_search_vector_values(updated_count: int = 0) -> None:
    products = list(
        Product.objects.filter(search_vector=None)
        .prefetch_related(*PRODUCT_FIELDS_TO_PREFETCH)
        .order_by("-id")[:BATCH_SIZE]
    )

    if not products:
        logger.info("No products to update.")
        return

    updated_count += set_search_vector_values(
        products,
        prepare_product_search_vector_value,
    )

    logger.info("Updated %d products", updated_count)

    if len(products) < BATCH_SIZE:
        logger.info("Setting product search document values finished.")
        return

    del products

    set_product_search_vector_values.delay(updated_count)


@celery_app.task(
    queue=settings.UPDATE_SEARCH_VECTOR_INDEX_QUEUE_NAME,
    expires=settings.BEAT_UPDATE_SEARCH_EXPIRE_AFTER_SEC,
)
def update_products_search_vector_task():
    products = Product.objects.filter(search_index_dirty=True).order_by()[
        :PRODUCTS_BATCH_SIZE
    ]
    update_products_search_vector(products, use_batches=False)


@celery_app.task(
    queue=settings.UPDATE_SEARCH_DOCUMENT_INDEX_QUEUE_NAME,
    expires=settings.BEAT_UPDATE_SEARCH_EXPIRE_AFTER_SEC,
)
def set_product_search_document_values(
    updated_count: int = 0,
) -> None:
    products = list(
        Product.objects.filter(search_document="")
        .prefetch_related(*PRODUCT_FIELDS_TO_PREFETCH)
        .order_by("-id")[:BATCH_SIZE]
    )

    if not products:
        logger.info("No products to update.")
        return

    updated_count += set_search_document_values(
        products, prepare_product_search_document
    )

    logger.info("Updated %d products", updated_count)

    if len(products) < BATCH_SIZE:
        logger.info("Setting product search document values finished.")
        return

    del products

    set_product_search_document_values.delay(updated_count)


@celery_app.task
def update_products_search_document_task():
    product_pks = (
        Product.objects.filter(search_document_dirty=True)
        .order_by("id")
        .values_list("id", flat=True)[:PRODUCTS_BATCH_SIZE]
    )
    products = Product.objects.filter(pk__in=product_pks)
    set_search_document_values(products, prepare_product_search_document)
    logger.info("Updated %d products", len(product_pks))
    return len(product_pks)
