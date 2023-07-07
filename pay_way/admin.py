from pay_way.models import PayWay
from django.contrib import admin


@admin.register(PayWay)
class PayWayAdmin(admin.ModelAdmin):
    list_display = ["name", "active", "cost", "free_for_order_amount"]
