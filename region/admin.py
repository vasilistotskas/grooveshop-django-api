from django.contrib import admin
from parler.admin import TranslatableAdmin

from region.models import Region


class RegionInline(admin.TabularInline):
    model = Region
    extra = 1


@admin.register(Region)
class RegionAdmin(TranslatableAdmin):
    search_fields = ["alpha", "country", "translations__name"]
