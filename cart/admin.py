from django.contrib import admin
from unfold.admin import ModelAdmin

from cart.models import Cart, CartItem


class CartItemInline(admin.TabularInline):
    model = CartItem
    extra = 0


@admin.register(Cart)
class CartAdmin(ModelAdmin):
    list_filter = ["id"]
    search_fields = ["id"]
    date_hierarchy = "updated_at"
    inlines = [CartItemInline]


admin.site.register(CartItem)
