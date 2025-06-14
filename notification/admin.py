from django.contrib import admin
from parler.admin import TranslatableAdmin
from unfold.admin import ModelAdmin

from notification.models.notification import Notification
from notification.models.user import NotificationUser


@admin.register(Notification)
class NotificationAdmin(TranslatableAdmin):
    list_display = [
        "id",
        "link",
        "kind",
        "title",
        "message",
        "created_at",
        "updated_at",
    ]
    list_filter = [
        "kind",
        "created_at",
        "updated_at",
    ]
    search_fields = [
        "translations__title",
        "translations__message",
    ]
    readonly_fields = [
        "created_at",
        "updated_at",
    ]
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "link",
                    "kind",
                    "title",
                    "message",
                )
            },
        ),
        (
            "System",
            {
                "fields": (
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )


@admin.register(NotificationUser)
class NotificationUserAdmin(ModelAdmin):
    list_display = [
        "id",
        "user",
        "notification",
        "seen",
        "created_at",
        "updated_at",
    ]
    list_filter = [
        "seen",
        "created_at",
        "updated_at",
    ]
    search_fields = [
        "user__email",
        "user__username",
    ]
    readonly_fields = [
        "created_at",
        "updated_at",
    ]
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "user",
                    "notification",
                    "seen",
                )
            },
        ),
        (
            "System",
            {
                "fields": (
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )
