from typing import Dict
from typing import Type

from rest_framework import serializers
from rest_framework.relations import PrimaryKeyRelatedField

from core.api.serializers import BaseExpandSerializer
from slider.models import Slide
from slider.models import Slider


class SliderSerializer(serializers.ModelSerializer):
    class Meta:
        model = Slider
        fields = (
            "id",
            "name",
            "url",
            "title",
            "description",
            "main_image_absolute_url",
            "main_image_filename",
            "thumbnail",
            "video",
        )


class SlideSerializer(BaseExpandSerializer):
    slider = PrimaryKeyRelatedField(queryset=Slider.objects.all())

    class Meta:
        model = Slide
        fields = (
            "id",
            "slider",
            "url",
            "title",
            "subtitle",
            "description",
            "discount",
            "button_label",
            "show_button",
            "date_start",
            "date_end",
            "order_position",
            "main_image_absolute_url",
            "main_image_filename",
            "thumbnail",
            "created_at",
            "updated_at",
            "sort_order",
            "uuid",
        )

    def get_expand_fields(self) -> Dict[str, Type[serializers.ModelSerializer]]:
        return {
            "slider": SliderSerializer,
        }
