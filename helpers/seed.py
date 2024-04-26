import mimetypes
import os

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile

from settings import BASE_DIR

DEFAULT_IMAGE_PATH = os.path.join(BASE_DIR, "static/images/no_photo.jpg")


def get_or_create_default_image(
    image_path: str, use_default_storage: bool = True
) -> SimpleUploadedFile:
    if use_default_storage:
        if default_storage.exists(image_path):
            with default_storage.open(image_path, "rb") as f:
                return create_uploaded_image(f.read(), image_path)
        else:
            img_data = read_default_image()
            default_storage.save(
                image_path,
                create_uploaded_image(img_data, image_path),
            )
            return create_uploaded_image(img_data, image_path)
    else:
        if os.path.exists(image_path):
            with open(image_path, "rb") as f:
                return create_uploaded_image(f.read(), image_path)
        else:
            img_data = read_default_image()
            with open(image_path, "wb") as img_file:
                img_file.write(img_data)
            return create_uploaded_image(img_data, image_path)


def read_default_image():
    with open(DEFAULT_IMAGE_PATH, "rb") as f:
        return f.read()


def create_uploaded_image(content, image_path):
    mime_type, _ = mimetypes.guess_type(image_path)
    if mime_type is None:
        mime_type = "application/octet-stream"

    return SimpleUploadedFile(
        name=os.path.basename(image_path),
        content=content,
        content_type=mime_type,
    )
