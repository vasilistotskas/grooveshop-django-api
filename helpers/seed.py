import os

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile

from app.settings import BASE_DIR


def get_or_create_default_image(image_path):
    if not default_storage.exists(image_path):
        default_path = os.path.join(BASE_DIR, "files/images") + "/no_photo.jpg"
        with open(default_path, "rb") as f:
            img = SimpleUploadedFile(
                name=os.path.basename(image_path),
                content=f.read(),
                content_type="image/jpeg",
            )
            default_storage.save(image_path, img)
    return image_path
