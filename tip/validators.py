import os

from django.core.exceptions import ValidationError


def validate_file_extension(value):
    ext = os.path.splitext(value.name)[1]  # [0] returns path & filename
    valid_extensions = [
        ".webp",
        ".svg",
        ".jpg",
        ".png",
        ".jpeg",
        ".gif",
        ".tiff",
        ".tif",
    ]  # populate with the extensions that you allow / want
    if not ext.lower() in valid_extensions:
        raise ValidationError("Unsupported file extension.")
