from django.contrib import admin

from user.models import UserAccount
from user.models.address import UserAddress


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
    search_fields = ["email", "phone", "first_name", "last_name"]


@admin.register(UserAddress)
class UserAddressAdmin(admin.ModelAdmin):
    list_display = [
        "user",
        "title",
        "first_name",
        "last_name",
        "street",
        "street_number",
        "city",
        "zipcode",
        "country",
        "region",
        "floor",
        "location_type",
        "phone",
        "mobile_phone",
        "notes",
        "is_main",
    ]
    list_filter = ["floor", "location_type"]
    search_fields = ["user__email", "title", "first_name", "last_name", "street"]
