from django.contrib import admin

from country.models import Country
from region.admin import RegionInline


@admin.register(Country)
class CountryAdmin(admin.ModelAdmin):
    inlines = [RegionInline]
