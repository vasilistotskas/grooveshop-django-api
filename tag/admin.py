from django.apps import apps
from django.contrib import admin
from django.contrib.contenttypes.admin import GenericTabularInline
from parler.admin import TranslatableAdmin

from tag.models.tag import Tag


class TagInLine(GenericTabularInline):
    autocomplete_fields = ["tag"]
    model = apps.get_model("tag", "TaggedItem")
    extra = 0


@admin.register(Tag)
class TagAdmin(TranslatableAdmin):
    search_fields = ["translations__label"]
