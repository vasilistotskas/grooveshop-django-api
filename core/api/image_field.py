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

from core.uploads import validate_servable_image


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
            validate_servable_image(upload, formats=self.formats)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                exc.messages[0], code=exc.code
            ) from exc
        return upload
