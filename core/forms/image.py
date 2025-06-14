from __future__ import annotations

import contextlib
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.core import validators
from django.core.exceptions import ValidationError
from django.forms import ImageField
from django.utils.translation import gettext_lazy as _

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
    default_validators = [validate_image_file_extension]
    DEFAULT_MAX_FILE_SIZE = 2621440

    def __init__(self, *args, max_file_size: int | None = None, **kwargs):
        if max_file_size is None:
            max_file_size = (
                settings.FILE_UPLOAD_MAX_MEMORY_SIZE
                or self.DEFAULT_MAX_FILE_SIZE
            )
        self.max_file_size = max_file_size
        super().__init__(*args, **kwargs)

    def to_python(self, data: File[Any] | None):
        if data is None:
            return None

        if (
            self.max_file_size
            and hasattr(data, "size")
            and data.size > self.max_file_size
        ):
            if self.max_file_size < 1048576:  # Less than 1MB
                size_str = f"{self.max_file_size / 1024:.1f}KB"
            else:
                size_str = f"{self.max_file_size / 1024 / 1024:.1f}MB"
            raise ValidationError(
                f"File size exceeds maximum allowed size of {size_str}",
                code="file_too_large",
            )

        try:
            f = super().to_python(data)
        except ValidationError as err:
            if err.code != "invalid_image":
                raise

            if not self.is_svg(data):
                raise ValidationError(
                    _(
                        "Upload a valid image. The file you uploaded was either not an image or a corrupted image."
                    ),
                    code="invalid_image",
                ) from err

            f = data
            if hasattr(f, "content_type"):
                f.content_type = "image/svg+xml"  # type: ignore[attr-defined]

            if hasattr(f, "seek") and callable(f.seek):
                f.seek(0)

        return f

    @staticmethod
    def is_svg(f: File[Any] | None) -> bool:
        if f is None:
            return False

        SVG_TAGS = ["{http://www.w3.org/2000/svg}svg", "svg"]

        original_position = None
        if hasattr(f, "tell") and callable(f.tell):
            with contextlib.suppress(OSError):
                original_position = f.tell()

        if hasattr(f, "seek") and callable(f.seek):
            f.seek(0)

        try:
            parser = ET.XMLParser()
            doc = ET.parse(f, parser=parser)
            root = doc.getroot()

            if root.tag not in SVG_TAGS:
                return False

            for elem in root.iter():
                tag_name = (
                    elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                )
                if tag_name.lower() == "script":
                    raise ValidationError(
                        _(
                            "SVG files containing script elements are not allowed for security reasons."
                        ),
                        code="svg_script_not_allowed",
                    )

                for attr in elem.attrib:
                    if attr.lower().startswith("on"):
                        raise ValidationError(
                            _(
                                "SVG files containing event handlers are not allowed for security reasons."
                            ),
                            code="svg_event_handler_not_allowed",
                        )

            return True

        except (ET.ParseError, ValueError):
            return False
        finally:
            if hasattr(f, "seek") and callable(f.seek):
                try:
                    if original_position is not None:
                        f.seek(original_position)
                    else:
                        f.seek(0)
                except OSError:
                    return False
