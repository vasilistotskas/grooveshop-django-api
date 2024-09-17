import importlib
from typing import Dict
from typing import override
from typing import Type

from drf_spectacular.utils import extend_schema_field
from parler_rest.fields import TranslatedFieldsField
from parler_rest.serializers import TranslatableModelSerializer
from rest_framework import serializers
from rest_framework.relations import PrimaryKeyRelatedField

from core.api.schema import generate_schema_multi_lang
from core.api.serializers import BaseExpandSerializer
from slider.models import Slide
from slider.models import Slider


@extend_schema_field(generate_schema_multi_lang(Slider))
class TranslatedFieldsFieldExtendSlider(TranslatedFieldsField):
    pass


@extend_schema_field(generate_schema_multi_lang(Slide))
class TranslatedFieldsFieldExtendSlide(TranslatedFieldsField):
    pass


class SliderSerializer(TranslatableModelSerializer, BaseExpandSerializer):
    translations = TranslatedFieldsFieldExtendSlider(shared_model=Slider)

    class Meta:
        model = Slider
        fields = (
            "id",
            "translations",
            "thumbnail",
            "video",
            "created_at",
            "updated_at",
            "uuid",
            "main_image_path",
        )
        read_only_fields = (
            "created_at",
            "updated_at",
            "uuid",
            "main_image_path",
        )


class SlideSerializer(TranslatableModelSerializer, BaseExpandSerializer):
    translations = TranslatedFieldsFieldExtendSlide(shared_model=Slide)
    slider = PrimaryKeyRelatedField(queryset=Slider.objects.all())

    class Meta:
        model = Slide
        fields = (
            "translations",
            "id",
            "slider",
            "discount",
            "show_button",
            "date_start",
            "date_end",
            "thumbnail",
            "sort_order",
            "created_at",
            "updated_at",
            "uuid",
            "main_image_path",
        )
        read_only_fields = (
            "created_at",
            "updated_at",
            "uuid",
            "main_image_path",
        )

    @override
    def get_expand_fields(
        self,
    ) -> Dict[str, Type[serializers.ModelSerializer]]:
        slider_serializer = importlib.import_module("slider.serializers").SliderSerializer
        return {
            "slider": slider_serializer,
        }
