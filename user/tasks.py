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
        image_filename = (
            f"image_{user.first_name}_{user.last_name}_{user.pk}.jpg"
        )
        user.image.save(image_filename, File(img_temp))
