import os

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile

from app.settings import BASE_DIR


def get_or_create_default_image(image_path: str) -> SimpleUploadedFile:
    if default_storage.exists(image_path):
        # If the image exists, open and return it as an image file
        with default_storage.open(image_path, "rb") as f:
            image = SimpleUploadedFile(
                name=os.path.basename(image_path),
                content=f.read(),
                content_type="image/jpeg",
            )
        return image
    else:
        # If the image doesn't exist, create and return the default image
        default_path = os.path.join(BASE_DIR, "files/images") + "/no_photo.jpg"
        with open(default_path, "rb") as f:
            img = SimpleUploadedFile(
                name=os.path.basename(image_path),
                content=f.read(),
                content_type="image/jpeg",
            )
            default_storage.save(image_path, img)
        return img
