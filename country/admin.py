from django.contrib import admin
from parler.admin import TranslatableAdmin

from country.models import Country
from region.admin import RegionInline


@admin.register(Country)
class CountryAdmin(TranslatableAdmin):
    search_fields = ("translations__name", "alpha_2", "alpha_3", "iso_cc", "phone_code")
    inlines = [RegionInline]
