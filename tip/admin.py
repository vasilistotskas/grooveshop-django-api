from django.contrib import admin
from parler.admin import TranslatableAdmin
from unfold.admin import ModelAdmin

from tip.models import Tip


@admin.register(Tip)
class TipAdmin(ModelAdmin, TranslatableAdmin):
    list_display = ["title", "kind", "active", "created_at", "image_tag"]
    list_filter = ["kind", "active"]
    search_fields = ["translations__title"]
