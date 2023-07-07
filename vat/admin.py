from django.contrib import admin

from vat.models import Vat


@admin.register(Vat)
class VatAdmin(admin.ModelAdmin):
    search_fields = ["value"]
