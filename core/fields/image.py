from typing import Any

from django.db.models.fields.files import ImageField
from django.forms import Field

from core.forms import image


class ImageAndSvgField(ImageField):
    def formfield(
        self,
        form_class: type[Field] | None = None,
        choices_form_class: type[Field] | None = None,
        **kwargs: Any,
    ) -> Field | None:
        if form_class is None:
            form_class = image.ImageAndSvgField

        return super().formfield(
            form_class=form_class,
            choices_form_class=choices_form_class,
            **kwargs,
        )
