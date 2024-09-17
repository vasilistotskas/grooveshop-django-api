from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import override
from typing import TYPE_CHECKING

from django.core import validators
from django.core.exceptions import ValidationError
from django.forms import ImageField

if TYPE_CHECKING:  # pragma: no cover
    from django.core.files import File  # isort:skip


def validate_image_file_extension(value: File | None) -> None:
    allowed_extensions = list(validators.get_available_image_extensions()) + ["svg"]
    validator = validators.FileExtensionValidator(allowed_extensions=allowed_extensions)
    return validator(value)


class ImageAndSvgField(ImageField):
    default_validators = [validate_image_file_extension]

    @override
    def to_python(self, data: File | None) -> File | None:
        try:
            f = super().to_python(data)
        except ValidationError as e:
            if e.code != "invalid_image":
                raise

            f = data
            if not self.is_svg(f):
                raise ValidationError("Invalid file format", code="invalid_image")

            f.content_type = "image/svg+xml"
            if hasattr(f, "seek") and callable(f.seek):
                f.seek(0)

        return f

    def is_svg(self, f: File | None) -> bool:
        if f is None:
            return False

        if hasattr(f, "seek") and callable(f.seek):
            f.seek(0)

        try:
            parser = ET.XMLParser()
            doc = ET.parse(f, parser=parser)
            root = doc.getroot()

            return root.tag == "{http://www.w3.org/2000/svg}svg"
        except ET.ParseError:
            return False
