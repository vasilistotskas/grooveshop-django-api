"""The API's image upload field: the same rule as the admin's.

``ServableImageField`` replaces DRF's ``ImageField`` for every model field
that is an ``ImageAndSvgField`` (registered once, in ``CoreConfig.ready``), so a
serializer cannot take an image the media service would show as a placeholder.
It checks the uploaded file, never a stored one.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from core.uploads import prepare_servable_image


class ServableImageField(serializers.ImageField):
    """``formats`` narrows the accepted formats for one serializer (a subset
    of the contract's names, e.g. no GIF for a product image)."""

    def __init__(
        self, *args: Any, formats: Collection[str] | None = None, **kwargs: Any
    ) -> None:
        self.formats = formats
        super().__init__(*args, **kwargs)

    def to_internal_value(self, data: Any) -> Any:
        upload = serializers.FileField.to_internal_value(self, data)
        try:
            upload, _image = prepare_servable_image(
                upload, formats=self.formats
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                exc.messages[0], code=exc.code
            ) from exc
        return upload


# ``main_image_width``/``main_image_height``: the size a browser shows the
# image ``main_image_path`` names at (EXIF orientation applied), as
# ``ImageAndSvgField`` stored it; null for an SVG, no image, or a row not
# measured yet. The storefront states the media service's rendition size
# from it (og:image:width/height, a schema.org ImageObject). Read from the
# row's own ``image_width``/``image_height``; a serializer whose image is on
# another row declares the two fields itself. A comment, not a docstring:
# drf-spectacular would publish a docstring as the description of every
# serializer that mixes this in.
class MainImageSizeMixin(serializers.Serializer):
    main_image_width = serializers.IntegerField(
        source="image_width", read_only=True, allow_null=True
    )
    main_image_height = serializers.IntegerField(
        source="image_height", read_only=True, allow_null=True
    )
