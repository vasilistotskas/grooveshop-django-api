from region.models import Region
from django.contrib import admin


class RegionInline(admin.TabularInline):
    model = Region
    extra = 1


@admin.register(Region)
class RegionAdmin(admin.ModelAdmin):
    search_fields = ["alpha", "alpha_2", "name"]
