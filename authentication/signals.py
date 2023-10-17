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

    picture_url = None
    if sociallogin.account.provider == "facebook":
        picture_url = (
            "http://graph.facebook.com/"
            + sociallogin.account.uid
            + "/picture?type=large"
        )

    if sociallogin.account.provider == "google":
        picture_url = sociallogin.account.extra_data["picture"]

    if picture_url:
        response = requests.get(picture_url)

        if response.status_code == 200:
            img_temp = NamedTemporaryFile(delete=True)
            img_temp.write(response.content)
            img_temp.flush()
            user.image.save(
                f"image_{user.first_name}_{user.last_name}_{user.pk}.jpg",
                File(img_temp),
            )
