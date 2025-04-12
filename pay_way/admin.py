from django.contrib import admin
from parler.admin import TranslatableAdmin
from unfold.admin import ModelAdmin

from pay_way.models import PayWay


@admin.register(PayWay)
class PayWayAdmin(ModelAdmin, TranslatableAdmin):
    list_display = ["name", "active", "cost", "free_for_order_amount"]
    list_filter = ("active",)
    search_fields = ("translations__name",)
