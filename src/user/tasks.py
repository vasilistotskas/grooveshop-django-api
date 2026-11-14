import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from core import celery_app
from core.net.safe_fetch import FetchError, safe_fetch
from core.retry import (
    MAIL_TRANSIENT_ERRORS,
    TRANSIENT_ERRORS,
    is_recipient_refused,
)
from core.tasks import MonitoredTask
from core.uploads import image_limits, prepare_servable_image

logger = logging.getLogger(__name__)


@celery_app.task(
    base=MonitoredTask,
    bind=True,
    name="Download Social Avatar",
    max_retries=3,
    autoretry_for=TRANSIENT_ERRORS,
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
        fetched = safe_fetch(
            picture_url, max_bytes=image_limits().largest_file_bytes
        )
    except FetchError as e:
        logger.error("Failed to retrieve image from %s: %s", picture_url, e)
        raise

    # The same rule as every other image path: the format and pixel
    # caps, and no GPS position in what is stored. A provider's file that
    # fails it is dropped, and nothing about it will change on a retry.
    upload = SimpleUploadedFile(
        "avatar", fetched.content, content_type=fetched.content_type
    )
    try:
        upload, source = prepare_servable_image(upload, allow_svg=False)
    except ValidationError as e:
        logger.warning(
            "Avatar from %s refused for user %s: %s",
            picture_url,
            user_id,
            "; ".join(e.messages),
        )
        return

    extension = "jpg" if source.format == "jpeg" else source.format
    image_filename = (
        f"image_{user.first_name}_{user.last_name}_{user.pk}.{extension}"
    )
    user.image.save(image_filename, upload)


@celery_app.task(
    base=MonitoredTask, bind=True, max_retries=3, default_retry_delay=300
)
def send_subscription_confirmation_email_task(
    self, subscription_id: int
) -> bool:
    """Send the confirmation email for a pending subscription.

    Retries 3 times, 300 s apart (``default_retry_delay``), when sending
    fails — ``send_subscription_confirmation`` lets the mail error
    through for exactly that. Safe to run multiple times — the helper is
    a no-op once the subscription is no longer PENDING. Serves account
    and guest (newsletter form) rows alike.
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
        return send_subscription_confirmation(subscription)
    except Exception as exc:
        if is_recipient_refused(exc):
            # Not a failure to retry: the mail server says nobody can
            # read this address.
            logger.warning(
                "Confirmation for subscription %s refused by the mail "
                "server: %s",
                subscription_id,
                exc,
            )
            subscription.mark_bounced()
            return False
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
    # The submitted address and the visitor's network identity are the
    # payload; keep them out of the failure log.
    sensitive_kwargs=frozenset({"email", "consent_ip", "consent_user_agent"}),
)
def subscribe_to_newsletter_task(
    self,
    *,
    topic_id: int,
    email: str,
    consent_text: str,
    consent_ip: str | None,
    consent_user_agent: str,
    language: str,
    consented_at: str,
) -> dict:
    """Everything the newsletter form does that depends on the address.

    ``NewsletterSubscribeView`` only validates the request and snapshots
    the consent, then always enqueues this one task and answers 202 — so
    the request's cost cannot tell a new address from a confirmed one.
    Here: the row lookup, the verified-owner attach, (re-)arming with the
    cooldown, the insert race, and queuing the email that answers the
    submission: the confirmation link, or, for an address that is already
    confirmed, the notice that says so (each carries its own retry
    policy). Runs in the requesting tenant's schema
    (``dispatch_on_commit`` pins it; ``TenantTask`` enters it).
    """
    from datetime import datetime

    from user.models.subscription import SubscriptionTopic
    from user.services.subscription import (
        NewsletterFormOutcome,
        dispatch_already_subscribed_notice,
        dispatch_confirmation,
        subscribe_email_to_newsletter,
    )

    topic = (
        SubscriptionTopic.objects.default_newsletter()
        .filter(pk=topic_id)
        .first()
    )
    if topic is None:
        # The topic stopped being the default between request and run.
        logger.info(
            "subscribe_to_newsletter_task: topic %s is no longer the "
            "default newsletter topic",
            topic_id,
        )
        return {"status": "skipped", "reason": "topic_unavailable"}

    result = subscribe_email_to_newsletter(
        topic=topic,
        email=email,
        consent_text=consent_text,
        consent_ip=consent_ip,
        consent_user_agent=consent_user_agent,
        language=language,
        consented_at=datetime.fromisoformat(consented_at),
    )
    if result is None:
        return {"status": "noop"}
    match result.outcome:
        case NewsletterFormOutcome.ARMED:
            dispatch_confirmation(result.subscription)
            return {
                "status": "armed",
                "subscription_id": result.subscription.id,
            }
        case NewsletterFormOutcome.ALREADY_SUBSCRIBED:
            dispatch_already_subscribed_notice(result.subscription)
            return {
                "status": "already_subscribed",
                "subscription_id": result.subscription.id,
            }


@celery_app.task(
    base=MonitoredTask,
    bind=True,
    max_retries=3,
    autoretry_for=MAIL_TRANSIENT_ERRORS,
    retry_backoff=True,
    retry_jitter=True,
)
def send_newsletter_already_subscribed_email_task(
    self, subscription_id: int
) -> bool:
    """Tell the owner of a confirmed subscription that it already exists.

    Serves account and guest (newsletter form) rows alike. The cooldown
    was claimed when the task was queued, so a retry re-sends the same
    notice and nothing else. A permanent refusal of every recipient (the
    address cannot receive mail) marks the row BOUNCED, as for the
    confirmation: nothing more is written to it, and a new submission
    starts over with a fresh confirmation link.
    """
    from user.models.subscription import UserSubscription
    from user.utils.subscription import send_already_subscribed_notice

    try:
        subscription = UserSubscription.objects.select_related(
            "user", "topic"
        ).get(id=subscription_id)
    except UserSubscription.DoesNotExist:
        logger.error(
            "Subscription %s not found for the already-subscribed notice",
            subscription_id,
        )
        return False
    try:
        return send_already_subscribed_notice(subscription)
    except Exception as exc:
        if not is_recipient_refused(exc):
            raise
        logger.warning(
            "Already-subscribed notice for subscription %s refused by the "
            "mail server: %s",
            subscription_id,
            exc,
        )
        subscription.mark_bounced()
        return False


@celery_app.task(
    base=MonitoredTask,
    bind=True,
    name="export_user_data",
    max_retries=3,
    autoretry_for=MAIL_TRANSIENT_ERRORS,
    retry_backoff=True,
    retry_jitter=True,
)
def export_user_data_task(self, export_id: int) -> dict:
    """Compile the user's data, write JSON to private media, email them.

    Idempotent: on retry it re-writes the file under the same path and
    emails them again.
    """
    import json
    import os

    from django.core.files.storage import FileSystemStorage
    from django.utils import timezone

    from emails.send import send_email, was_sent
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
        # way to reach them is the owner's download,
        # ``UserAccountViewSet.download_data_export``.
        location = get_export_location()
        storage = FileSystemStorage(location=location)

        # A rerun keeps the file it already named (a bundle written before
        # v3.231.3 is named by its old token): writing a second one would
        # leave the first where neither the expiry sweep nor an erasure finds it.
        rel_path = export.file_path or f"{user.id}/{export.uuid}.json"
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

        if not was_sent(send_email("user.data_export_ready", export)):
            logger.warning(
                "export_user_data_task: export %s ready but the email was "
                "not sent",
                export_id,
            )

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
    name="push_force_logout",
    max_retries=3,
    # ``send_to_user_group`` raises the builtin ``TimeoutError`` at its
    # deadline, which ``TRANSIENT_ERRORS`` already retries.
    autoretry_for=TRANSIENT_ERRORS,
    retry_backoff=True,
    soft_time_limit=settings.NOTIFICATION_TASK_SOFT_TIME_LIMIT,
    time_limit=settings.NOTIFICATION_TASK_TIME_LIMIT,
)
def push_force_logout(self, user_id: int) -> None:
    """Tell the user's open sockets that their tokens were revoked.

    Queued after a password change, a password reset, an email change or
    an account deletion, which revoke the tokens themselves before this
    runs: the push only closes the tabs sooner, so it never decides the
    request's answer (#673).
    """
    from channels.layers import get_channel_layer

    from notification.groups import send_to_user_group

    layer = get_channel_layer()
    if layer:
        send_to_user_group(layer, user_id, {"type": "force.logout"})


@celery_app.task(
    base=MonitoredTask,
    bind=True,
    name="delete_user_account",
    max_retries=2,
    autoretry_for=TRANSIENT_ERRORS,
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

    User = get_user_model()

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


@celery_app.task(
    base=MonitoredTask, bind=True, max_retries=3, default_retry_delay=300
)
def cleanup_expired_data_exports(self) -> dict:
    """Delete the PII bundle files of expired GDPR exports and mark them
    EXPIRED.

    Right-of-access exports are written to the private media PVC and are only
    valid for ``EXPORT_TTL``. Without this cleanup the exported personal data
    lingered on disk indefinitely past its expiry (G0435). Registered as a
    periodic beat task.
    """
    import os

    from django.utils import timezone

    from user.models.data_export import UserDataExport
    from user.services.gdpr import get_export_location

    now = timezone.now()
    location = get_export_location()

    stale = UserDataExport.objects.filter(expires_at__lt=now).exclude(
        status=UserDataExport.Status.EXPIRED
    )

    expired = 0
    stranded = 0
    for export in stale:
        if export.file_path:
            abs_path = os.path.join(location, export.file_path)
            try:
                if os.path.exists(abs_path):
                    os.remove(abs_path)
            except OSError:
                # Clearing file_path here would be the last reference to
                # a bundle holding the subject's complete personal data,
                # past its TTL, with nothing left that could ever find
                # it again. Leave the row pending so the next run
                # retries, and log at ERROR: an undeleted export past
                # expiry is a retention breach, not a warning.
                logger.exception(
                    "cleanup_expired_data_exports: could not remove %s for "
                    "export %s — the bundle is still on disk past its "
                    "expiry; leaving the row pending for the next run",
                    abs_path,
                    export.pk,
                )
                stranded += 1
                continue

        export.status = UserDataExport.Status.EXPIRED
        export.file_path = ""
        export.file_size = 0
        export.save(
            update_fields=[
                "status",
                "file_path",
                "file_size",
                "updated_at",
            ]
        )
        expired += 1

    logger.info(
        "cleanup_expired_data_exports: expired %s export(s), %s stranded",
        expired,
        stranded,
    )
    return {"status": "success", "expired": expired, "stranded": stranded}


#: A guest newsletter signup that was never confirmed within this window
#: is deleted: the address and its consent record were given for a
#: subscription that never came into being.
UNCONFIRMED_GUEST_SUBSCRIPTION_RETENTION_DAYS = 30


@celery_app.task(
    base=MonitoredTask, bind=True, max_retries=3, default_retry_delay=300
)
def purge_unconfirmed_guest_subscriptions(self) -> dict:
    """Delete guest subscriptions still PENDING 30 days after their last
    confirmation email. Registered as a periodic beat task (fanned out
    per tenant). Account rows are never touched: they are the account's
    own preferences, and erasure of those goes through the account.
    """
    from datetime import timedelta

    from django.utils import timezone

    from user.models.subscription import UserSubscription

    cutoff = timezone.now() - timedelta(
        days=UNCONFIRMED_GUEST_SUBSCRIPTION_RETENTION_DAYS
    )
    deleted, _ = UserSubscription.objects.filter(
        user__isnull=True,
        status=UserSubscription.SubscriptionStatus.PENDING,
        confirmation_sent_at__lt=cutoff,
    ).delete()
    logger.info(
        "purge_unconfirmed_guest_subscriptions: deleted %s row(s)", deleted
    )
    return {"status": "success", "deleted": deleted}


@celery_app.task(
    base=MonitoredTask,
    bind=True,
    # The class decides what MAY retry; ``MonitoredTask.retry`` then
    # stops on a 5xx reply code (``core.retry.is_permanent_failure``).
    autoretry_for=MAIL_TRANSIENT_ERRORS,
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=5,
    # A password reset link, a login code and a verification code are the
    # secret: keep the context out of ``MonitoredTask.on_failure``'s log.
    sensitive_kwargs=frozenset({"context"}),
)
def send_allauth_email_task(
    self,
    *,
    type_key: str,
    address: str,
    language: str,
    user_pk: int | None,
    context: dict[str, str],
) -> dict[str, object]:
    """Send one allauth mail through the email pipeline.

    Queued by ``UserAccountAdapter.send_mail`` so allauth's SMTP
    conversation is not part of the view: a 421 from the mail server is a
    retry here, not a 500 from sign-up. A transport failure propagates
    (``send_email`` re-raises it) for the retry policy above.

    An account deleted between the request and this task gets no mail.
    """
    from emails.send import send_email, was_sent
    from user.emails import AllauthMail
    from user.models.account import UserAccount

    user = None
    if user_pk is not None:
        user = UserAccount.objects.filter(pk=user_pk).first()
        if user is None:
            logger.warning(
                "send_allauth_email_task: account %s is gone, %s not sent",
                user_pk,
                type_key,
            )
            return {"status": "skipped", "reason": "account_deleted"}
    deliveries = send_email(
        type_key,
        AllauthMail(
            address=address, language=language, user=user, context=context
        ),
    )
    return {"status": "sent" if was_sent(deliveries) else "skipped"}
