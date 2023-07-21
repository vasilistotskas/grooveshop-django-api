from django.contrib import admin

from user.models import UserAccount
from user.models.address import UserAddress


class AddressInline(admin.TabularInline):
    model = UserAddress
    extra = 1


@admin.register(UserAccount)
class UserAccountAdmin(admin.ModelAdmin):
    list_display = [
        "email",
        "first_name",
        "last_name",
        "phone",
        "email",
        "city",
        "zipcode",
        "address",
        "place",
        "country",
        "region",
        "image_tag",
        "is_active",
        "is_staff",
    ]
    inlines = [AddressInline]
    search_fields = ["email", "phone", "first_name", "last_name"]
