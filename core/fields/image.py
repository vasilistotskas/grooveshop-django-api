from django.db.models.fields.files import ImageField

from core.forms import image


class ImageAndSvgField(ImageField):
    def formfield(self, **kwargs):
        return super().formfield(
            **{
                "form_class": image.ImageAndSvgField,
                **kwargs,
            }
        )
