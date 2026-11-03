from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django import forms
from django.core import validators
from django.core.exceptions import ValidationError
from django.forms import ImageField

from core.uploads import is_safe_svg, validate_servable_image

if TYPE_CHECKING:  # pragma: no cover
    from django.core.files import File


def validate_image_file_extension(value: File[Any] | None):
    if value is None:
        return None

    allowed_extensions = [
        *list(validators.get_available_image_extensions()),
        "svg",
    ]
    validator = validators.FileExtensionValidator(
        allowed_extensions=allowed_extensions
    )
    return validator(value)


class ImageAndSvgField(ImageField):
    """An image upload, or a safe SVG, that the media service will serve.

    The rule is ``core.uploads.validate_servable_image``: the media service's
    own per-format size caps and pixel budget (the admin's old flat 2.5 MB cap
    was Django's in-memory upload threshold, ``FILE_UPLOAD_MAX_MEMORY_SIZE``,
    which says where a file is held while it uploads, not how large an image
    may be).
    """

    default_validators = [validate_image_file_extension]

    def to_python(self, data: File[Any] | None):
        if data is None:
            return None

        validate_servable_image(data)

        try:
            f = super().to_python(data)
        except ValidationError as err:
            if err.code != "invalid_image":
                raise
            # Django's ImageField does not read an SVG; the rule above has
            # already found it a safe one.
            f = data
            if hasattr(f, "content_type"):
                f.content_type = "image/svg+xml"

            if hasattr(f, "seek") and callable(f.seek):
                f.seek(0)

        return f

    @staticmethod
    def is_svg(f: File[Any] | None) -> bool:
        return is_safe_svg(f)


class ImageUploadForm(forms.Form):
    file = ImageAndSvgField()
