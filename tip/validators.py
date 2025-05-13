import os

from django.core.exceptions import ValidationError


def validate_file_extension(value):
    ext = os.path.splitext(value.name)[1]
    valid_extensions = [
        ".webp",
        ".svg",
        ".jpg",
        ".png",
        ".jpeg",
        ".gif",
        ".tiff",
        ".tif",
    ]
    if ext.lower() not in valid_extensions:
        raise ValidationError("Unsupported file extension.")
