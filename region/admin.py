from django.contrib import admin
from parler.admin import TranslatableAdmin, TranslatableTabularInline
from unfold.admin import ModelAdmin

from region.models import Region


class RegionInline(TranslatableTabularInline):
    model = Region
    extra = 1


@admin.register(Region)
class RegionAdmin(ModelAdmin, TranslatableAdmin):
    search_fields = ["alpha", "country", "translations__name"]
