import logging
from tempfile import NamedTemporaryFile

import requests
from celery import shared_task
from django.core.files import File

logger = logging.getLogger(__name__)


@shared_task(
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


@shared_task(bind=True, max_retries=3, default_retry_delay=300)
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
