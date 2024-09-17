from typing import override

from django.db.models.fields.files import ImageField

from core.forms import image


class ImageAndSvgField(ImageField):
    @override
    def formfield(self, **kwargs):
        return super().formfield(
            **{
                "form_class": image.ImageAndSvgField,
                **kwargs,
            }
        )
