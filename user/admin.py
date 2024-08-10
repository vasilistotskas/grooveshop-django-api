from allauth.account.decorators import secure_admin_login
from django.conf import settings
from django.contrib import admin

from user.models import UserAccount
from user.models.address import UserAddress


if settings.DJANGO_ADMIN_FORCE_ALLAUTH:
    admin.autodiscover()
    admin.site.login = secure_admin_login(admin.site.login)


@admin.register(UserAccount)
class UserAccountAdmin(admin.ModelAdmin):
    list_display = [
        "email",
        "username",
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
    search_fields = ["email", "username", "phone", "first_name", "last_name"]


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
    search_fields = [
        "user__email",
        "user__username",
        "title",
        "first_name",
        "last_name",
        "street",
    ]
