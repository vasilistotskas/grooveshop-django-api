from typing import override

from django.contrib import admin

from contact.models import Contact


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ["name", "email", "message", "created_at"]
    list_filter = ["created_at"]
    search_fields = ["name", "email"]
    readonly_fields = ("created_at",)
    date_hierarchy = "created_at"

    @override
    def get_ordering(self, request):
        return ["-created_at", "name"]
