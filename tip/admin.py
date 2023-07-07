from django.contrib import admin

from tip.models import Tip


@admin.register(Tip)
class TipAdmin(admin.ModelAdmin):
    list_display = ["title", "kind", "active", "created_at", "image_tag"]
