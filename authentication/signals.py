import logging
from tempfile import NamedTemporaryFile

import requests
from allauth.account.signals import user_signed_up
from django.core.files import File
from django.dispatch import receiver

logger = logging.getLogger(__name__)


@receiver(user_signed_up)
def populate_profile(sociallogin=None, user=None, **kwargs):
    if not sociallogin or not user:
        logger.warning("No sociallogin or user passed to populate_profile")
        return

    provider = sociallogin.account.provider
    picture_url = None

    match provider:
        case "facebook":
            picture_url = f"http://graph.facebook.com/{sociallogin.account.uid}/picture?type=large"
        case "google":
            picture_url = sociallogin.account.extra_data.get("picture")
        case "discord":
            avatar_id = sociallogin.account.extra_data.get("id")
            avatar_hash = sociallogin.account.extra_data.get("avatar")
            if avatar_id and avatar_hash:
                picture_url = f"https://cdn.discordapp.com/avatars/{avatar_id}/{avatar_hash}.png"
            else:
                logger.warning(
                    "Missing avatar_id or avatar_hash for Discord provider"
                )
        case "github":
            picture_url = sociallogin.account.extra_data.get("avatar_url")
        case _:
            logger.warning(f"Unsupported provider: {provider}")

    if picture_url:
        try:
            response = requests.get(picture_url, timeout=10)
            response.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"Failed to retrieve image from {picture_url}: {e}")
            return

        with NamedTemporaryFile(delete=True) as img_temp:
            img_temp.write(response.content)
            img_temp.flush()
            image_filename = (
                f"image_{user.first_name}_{user.last_name}_{user.pk}.jpg"
            )
            user.image.save(image_filename, File(img_temp))
