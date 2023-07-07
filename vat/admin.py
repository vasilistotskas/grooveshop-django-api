from vat.models import Vat
from django.contrib import admin


@admin.register(Vat)
class VatAdmin(admin.ModelAdmin):
    search_fields = ["value"]
