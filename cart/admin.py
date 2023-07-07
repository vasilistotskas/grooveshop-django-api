from cart.models import Cart
from cart.models import CartItem
from django.contrib import admin


class CartItemInline(admin.TabularInline):
    model = CartItem
    extra = 0


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_filter = ["id"]
    search_fields = ["id"]
    date_hierarchy = "updated_at"
    inlines = [CartItemInline]


admin.site.register(CartItem)
