from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.models import Group
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin
from unfold.forms import (
    AdminPasswordChangeForm,
    UserChangeForm,
    UserCreationForm,
)

from user.models import UserAccount
from user.models.address import UserAddress

admin.site.unregister(Group)


@admin.register(UserAccount)
class UserAdmin(ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm

    list_display = [
        "email",
        "username",
        "first_name",
        "last_name",
        "phone",
        "country",
        "region",
        "is_active",
        "is_staff",
    ]
    list_filter = ["is_active", "is_staff", "is_superuser", "country", "region"]
    search_fields = [
        "email",
        "username",
        "phone",
        "first_name",
        "last_name",
        "city",
        "address",
    ]
    ordering = ["-created_at"]

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (
            _("Personal info"),
            {
                "fields": (
                    "username",
                    "first_name",
                    "last_name",
                    "phone",
                    "birth_date",
                    "bio",
                    "image",
                )
            },
        ),
        (
            _("Contact info"),
            {
                "fields": (
                    "address",
                    "city",
                    "zipcode",
                    "place",
                    "country",
                    "region",
                ),
            },
        ),
        (
            _("Social media"),
            {
                "fields": (
                    "website",
                    "twitter",
                    "facebook",
                    "instagram",
                    "linkedin",
                    "youtube",
                    "github",
                ),
            },
        ),
        (
            _("Permissions"),
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                ),
            },
        ),
        (_("Important dates"), {"fields": ("created_at", "updated_at")}),
    )

    readonly_fields = ["created_at", "updated_at"]

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "username"),
            },
        ),
    )

    def get_readonly_fields(self, request, obj=None):
        readonly_fields = list(self.readonly_fields)
        if obj:
            readonly_fields.extend(["created_at", "updated_at"])
        return readonly_fields


@admin.register(UserAddress)
class UserAddressAdmin(ModelAdmin):
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


@admin.register(Group)
class GroupAdmin(BaseGroupAdmin, ModelAdmin):
    pass
