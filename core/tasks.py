from __future__ import absolute_import
from __future__ import unicode_literals

import logging
import os
import time
from datetime import datetime
from datetime import timedelta

from celery import shared_task
from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import management
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q
from django.db.models import Value
from django.template.loader import render_to_string
from django.utils.timezone import now

from app import celery_app
from core.postgres import FlatConcatSearchVector
from core.postgres import NoValidationSearchVector
from product.models.product import Product

User = get_user_model()
logger = logging.getLogger("celery")
languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]


@shared_task(bind=True, name="Clear Expired Sessions Task")
def clear_expired_sessions_task(self):
    try:
        management.call_command("clearsessions", verbosity=0)
        return "All expired sessions deleted."
    except Exception as e:
        return f"error: {e}"


@shared_task(bind=True, name="Clear All Cache Task")
def clear_all_cache_task(self):
    try:
        management.call_command("clear_cache", verbosity=0)
        return "All cache deleted."
    except Exception as e:
        return f"error: {e}"


@shared_task(bind=True, name="Clear Carts For None Users Task")
def clear_carts_for_none_users_task(self):
    from cart.models import Cart

    with transaction.atomic():
        null_carts = Cart.objects.filter(user=None).prefetch_related("cart_item_cart")
        cart_items_count = sum(cart.cart_item_cart.count() for cart in null_carts)
        carts_count = null_carts.count()
        null_carts.delete()

        message = f"Cleared {carts_count} null carts and {cart_items_count} related cart items."
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
def clear_blacklisted_tokens_task(self):
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


def get_postgres_search_config(language_code: str) -> str:
    language_configs = settings.PARLER_LANGUAGES.get(settings.SITE_ID, ())
    for lang_config in language_configs:
        if lang_config.get("code") == language_code:
            return lang_config.get("name", "").lower()
    return "simple"


def prepare_product_translation_search_vector_value(
    product: "Product", language_code: str, config="simple"
) -> FlatConcatSearchVector:
    translation = product.translations.get(language_code=language_code)
    search_vectors = [
        NoValidationSearchVector(Value(translation.name), config=config, weight="A"),
        NoValidationSearchVector(
            Value(translation.description), config=config, weight="C"
        ),
    ]

    return FlatConcatSearchVector(*search_vectors)


def prepare_product_translation_search_document(
    product: "Product", language_code: str
) -> str:
    translation = product.translations.get(language_code=language_code)
    document_parts = [translation.name, translation.description]
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
        config = get_postgres_search_config(language_code)

        for translation in translations.iterator():
            translation.search_vector = prepare_product_translation_search_vector_value(
                translation.master, language_code, config
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


@celery_app.task
def send_inactive_user_notifications():
    cutoff_date = now() - timedelta(days=30)
    inactive_users = User.objects.filter(last_login__lt=cutoff_date)

    for user in inactive_users:
        mail_subject = "We miss you!"
        message = render_to_string(
            "inactive_user_email_template.html",
            {
                "user": user,
                "app_base_url": settings.NUXT_BASE_URL,
            },
        )

        send_mail(
            subject=mail_subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
            html_message=message,
        )

    return f"Sent re-engagement emails to {inactive_users.count()} inactive users."


@celery_app.task
def monitor_system_health():
    try:
        from django.db import connections

        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()

    except Exception as e:
        logger.error(f"System health check failed: {e}")

        send_mail(
            subject="System Health Check Alert",
            message=f"An error occurred during system health checks: {e}",
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[settings.ADMIN_EMAIL],
            fail_silently=False,
        )
        return "System health check failed."

    return "System health check passed."


@celery_app.task
def backup_database():
    start_time = time.time()
    try:
        management.call_command("dbbackup", verbosity=3)
        end_time = time.time()
        execution_time = end_time - start_time
        return (
            f"Database backup completed successfully in {execution_time:.2f} seconds."
        )
    except Exception as e:
        return f"error: {e}"


@celery_app.task
def cleanup_old_database_backups(days=30):
    backups_path = os.path.join(settings.BASE_DIR, "backups")
    if not os.path.exists(backups_path):
        return "Backups directory does not exist."

    deleted_files_count = 0
    folder = os.listdir(backups_path)

    for filename in folder:
        if not filename.endswith(".psql.bin"):
            continue

        try:
            date_str = "-".join(filename.split("-")[2:5])
            file_date = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue

        if datetime.now() - file_date > timedelta(days=days):
            file_path = os.path.join(backups_path, filename)
            os.remove(file_path)
            deleted_files_count += 1

    message = (
        f"Deleted {deleted_files_count} database backup files older than {days} days."
    )
    return message


@celery_app.task
def optimize_images():
    images_path = os.path.join(settings.BASE_DIR, "static", "images")

    from PIL import Image

    for subdir, dirs, files in os.walk(images_path):
        for file in files:
            filepath = os.path.join(subdir, file)
            allowed_extensions = [".jpg", ".jpeg", ".png", ".webp"]
            if not any(filepath.endswith(ext) for ext in allowed_extensions):
                continue

            try:
                with Image.open(filepath) as img:
                    img.save(filepath, optimize=True, quality=85)
            except Exception as e:
                logger.error(f"Error optimizing image: {e}")

    return "Images optimized successfully."
