from typing import List

import admin_thumbnails
from slider.models import Slide
from slider.models import Slider
from django.contrib import admin


@admin_thumbnails.thumbnail("image")
class SliderSlidesInline(admin.StackedInline):
    model = Slide
    exclude: List[str] = []
    readonly_fields = ("id", "thumbnail")
    extra = 0


@admin.register(Slider)
class SliderAdmin(admin.ModelAdmin):
    list_display = ["id", "title", "image_tag"]
    search_fields = ["id", "title"]
    inlines = [SliderSlidesInline]
    prepopulated_fields = {"title": ("name",)}
    readonly_fields = ("image_tag", "thumbnail")
    actions = [""]


@admin.register(Slide)
class SlideAdmin(admin.ModelAdmin):
    list_display = ["id", "title", "image_tag", "order_position"]
    search_fields = ["id", "title", "slider__name"]
    readonly_fields = ("image_tag", "thumbnail")
    actions = [""]
