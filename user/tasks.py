import logging
from tempfile import NamedTemporaryFile

import requests
from django.core.files import File

from core import celery_app
from core.tasks import MonitoredTask

logger = logging.getLogger(__name__)


@celery_app.task(
    base=MonitoredTask,
    bind=True,
    name="Download Social Avatar",
    max_retries=3,
    autoretry_for=(requests.RequestException,),
    retry_backoff=True,
)
def download_social_avatar_task(self, user_id: int, picture_url: str):
    """Download a social provider avatar and save it to the user's image field."""
    from django.contrib.auth import get_user_model

    User = get_user_model()

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.error("User %s not found for avatar download", user_id)
        return

    try:
        response = requests.get(picture_url, timeout=10)
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error("Failed to retrieve image from %s: %s", picture_url, e)
        raise

    with NamedTemporaryFile(delete=True) as img_temp:
        img_temp.write(response.content)
        img_temp.flush()
        img_temp.seek(0)
        image_filename = (
            f"image_{user.first_name}_{user.last_name}_{user.pk}.jpg"
        )
        user.image.save(image_filename, File(img_temp))


@celery_app.task(
    base=MonitoredTask, bind=True, max_retries=3, default_retry_delay=300
)
def send_subscription_confirmation_email_task(
    self, subscription_id: int
) -> bool:
    """Send the confirmation email for a pending subscription.

    Retries 3× with 300s backoff on SMTP failure. Safe to run multiple times —
    the underlying helper is a no-op when the subscription is no longer
    PENDING or when the user already has an ACTIVE subscription for the topic.
    """
    from user.models.subscription import UserSubscription
    from user.utils.subscription import send_subscription_confirmation

    try:
        subscription = UserSubscription.objects.select_related(
            "user", "topic"
        ).get(id=subscription_id)
    except UserSubscription.DoesNotExist:
        logger.error(
            "Subscription %s not found for confirmation email", subscription_id
        )
        return False

    try:
        return send_subscription_confirmation(subscription, subscription.user)
    except Exception as exc:
        logger.error(
            "Error sending subscription confirmation for %s: %s",
            subscription_id,
            exc,
        )
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc) from exc
        return False


@celery_app.task(
    base=MonitoredTask,
    bind=True,
    name="export_user_data",
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
)
def export_user_data_task(self, export_id: int) -> dict:
    """Compile the user's data, write JSON to private media, email a link.

    Idempotent: on retry it re-writes the file under the same path and
    re-emails the link (the token column keeps the URL stable).
    """
    import json
    import os

    from django.conf import settings
    from django.core.files.storage import FileSystemStorage
    from django.core.mail import EmailMultiAlternatives
    from django.template.loader import render_to_string
    from django.utils import timezone
    from django.utils.translation import gettext as _
    from django.utils.translation import override

    from core.utils.i18n import get_user_language
    from core.utils.tenant_urls import (
        get_tenant_base_url,
        get_tenant_frontend_url,
    )
    from user.models.data_export import UserDataExport
    from user.services.gdpr import (
        EXPORT_TTL,
        compile_user_data,
        get_export_location,
    )

    try:
        export = UserDataExport.objects.select_related("user").get(id=export_id)
    except UserDataExport.DoesNotExist:
        logger.error("export_user_data_task: export %s not found", export_id)
        return {"status": "skipped", "reason": "export_not_found"}

    user = export.user
    export.status = UserDataExport.Status.PROCESSING
    export.save(update_fields=["status", "updated_at"])

    try:
        payload = compile_user_data(user)
        data_bytes = json.dumps(payload, ensure_ascii=False, indent=2).encode(
            "utf-8"
        )

        # Write under ``PRIVATE_MEDIA_ROOT/_gdpr_exports/`` — the
        # ``mediafiles_private`` shared PVC is the only writable path
        # mounted into both backend + celery_worker pods. Public
        # ``MEDIA_ROOT`` is a read-only container-local dir on the
        # worker, so writing there raises PermissionError. These files
        # are never served by the Django media view directly: the only
        # way to reach them is through the signed-token download
        # endpoint in ``user/views/data_export.py``.
        location = get_export_location()
        storage = FileSystemStorage(location=location)

        rel_path = f"{user.id}/{export.token}.json"
        abs_path = os.path.join(location, rel_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "wb") as fh:
            fh.write(data_bytes)

        export.file_path = rel_path
        export.file_size = len(data_bytes)
        export.status = UserDataExport.Status.READY
        export.expires_at = timezone.now() + EXPORT_TTL
        export.save(
            update_fields=[
                "file_path",
                "file_size",
                "status",
                "expires_at",
                "updated_at",
            ]
        )

        download_url = get_tenant_frontend_url(
            f"/account/settings/privacy?export={export.token}"
        )

        context = {
            "user": user,
            "download_url": download_url,
            "expires_at": export.expires_at,
            "file_size_kb": round(export.file_size / 1024, 1),
            "SITE_NAME": settings.SITE_NAME,
            "INFO_EMAIL": settings.INFO_EMAIL,
            "SITE_URL": get_tenant_base_url(),
            "STATIC_BASE_URL": settings.STATIC_BASE_URL,
        }

        with override(get_user_language(user)):
            subject = _("Your data export is ready")
            html_body = render_to_string(
                "emails/user/data_export_ready.html", context
            )
            text_body = render_to_string(
                "emails/user/data_export_ready.txt", context
            )

        msg = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[user.email],
            reply_to=[settings.INFO_EMAIL],
        )
        msg.attach_alternative(html_body, "text/html")
        msg.send(fail_silently=False)

        logger.info(
            "export_user_data_task: export %s ready (%s bytes) for user %s",
            export_id,
            export.file_size,
            user.id,
        )
        # Note: ``storage`` is imported for parity with invoice pattern
        # and to make future AWS switch one-line; we write via raw open()
        # here because JSON is small and we want atomic semantics.
        del storage
        return {
            "status": "success",
            "export_id": export_id,
            "size_bytes": export.file_size,
        }
    except Exception as exc:
        export.status = UserDataExport.Status.FAILED
        export.error_message = str(exc)[:2000]
        export.save(update_fields=["status", "error_message", "updated_at"])
        logger.exception(
            "export_user_data_task: failed for export %s", export_id
        )
        raise


@celery_app.task(
    base=MonitoredTask,
    bind=True,
    name="delete_user_account",
    max_retries=2,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def delete_user_account_task(self, user_id: int) -> dict:
    """Async right-to-erasure.

    The viewset does the password re-auth synchronously (so a failure
    surfaces as a 401 to the user) and then hands off to this task for
    the actual scrub, which can take seconds on accounts with lots of
    orders.
    """
    from django.contrib.auth import get_user_model

    from user.services.gdpr import anonymise_and_delete_user

    User = get_user_model()  # noqa: N806

    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        logger.info(
            "delete_user_account_task: user %s already gone — nothing to do",
            user_id,
        )
        return {"status": "noop", "reason": "already_deleted"}

    counts = anonymise_and_delete_user(user)
    return {"status": "success", "counts": counts}
