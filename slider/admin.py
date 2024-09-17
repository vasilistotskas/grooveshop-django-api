from typing import List
from typing import override

import admin_thumbnails
from django.contrib import admin
from parler.admin import TranslatableAdmin

from slider.models import Slide
from slider.models import Slider


@admin_thumbnails.thumbnail("image")
class SliderSlidesInline(admin.StackedInline):
    model = Slide
    exclude: List[str] = []
    readonly_fields = ("id", "thumbnail")
    extra = 0


@admin.register(Slider)
class SliderAdmin(TranslatableAdmin):
    list_display = ["id", "title", "image_tag"]
    search_fields = ["id", "translations__title"]
    inlines = [SliderSlidesInline]
    readonly_fields = ("image_tag", "thumbnail")
    actions = [""]

    @override
    def get_prepopulated_fields(self, request, obj=None):
        # can't use `prepopulated_fields = ..` because it breaks the admin validation
        # for translated fields. This is the official django-parler workaround.
        return {
            "title": ("name",),
        }


@admin.register(Slide)
class SlideAdmin(TranslatableAdmin):
    list_display = ["id", "title", "image_tag"]
    search_fields = ["id", "translations__title", "slider__translations__name"]
    readonly_fields = ("image_tag", "thumbnail")
    actions = [""]
