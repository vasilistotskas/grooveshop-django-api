from django.contrib import admin
from unfold.admin import ModelAdmin

from vat.models import Vat


@admin.register(Vat)
class VatAdmin(ModelAdmin):
    search_fields = ["value"]
