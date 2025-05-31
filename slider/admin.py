import admin_thumbnails
from django.contrib import admin
from parler.admin import TranslatableAdmin
from unfold.admin import ModelAdmin

from slider.models import Slide, Slider


@admin_thumbnails.thumbnail("image")
class SliderSlidesInline(admin.StackedInline):
    model = Slide
    readonly_fields = ("id",)
    extra = 0


@admin.register(Slider)
class SliderAdmin(ModelAdmin, TranslatableAdmin):
    list_display = ["id", "title"]
    search_fields = ["id", "translations__title"]
    inlines = [SliderSlidesInline]
    actions = [""]

    def get_prepopulated_fields(self, request, obj=None):
        return {
            "title": ("name",),
        }


@admin.register(Slide)
class SlideAdmin(ModelAdmin, TranslatableAdmin):
    list_display = ["id", "title"]
    search_fields = ["id", "translations__title", "slider__translations__name"]
    actions = [""]
