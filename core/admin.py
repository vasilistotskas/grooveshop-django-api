import csv
import json
import xml.etree.ElementTree as ET

from django import forms
from django.contrib import admin
from django.core.serializers.json import DjangoJSONEncoder
from django.http import HttpResponse
from django.utils.html import format_html

from core.models import Settings


class SettingsAdminForm(forms.ModelForm):
    class Meta:
        model = Settings
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super(SettingsAdminForm, self).__init__(*args, **kwargs)
        if not self.instance.pk:
            self.fields["key"].initial = "APP_"

    def clean(self):
        cleaned_data = super().clean()
        key = cleaned_data.get("key")
        if not key.startswith("APP_"):
            self.add_error("key", "Setting key must start with 'APP_'.")
        return cleaned_data


@admin.register(Settings)
class SettingsAdmin(admin.ModelAdmin):
    form = SettingsAdminForm
    list_display = ("key", "value_display", "value_type", "is_public", "description")
    list_filter = ("is_public", "value_type")
    search_fields = ("key",)
    readonly_fields = ("value_display",)

    def value_display(self, obj):
        try:
            value = json.loads(obj.value)
            pretty_value = json.dumps(value, indent=4, cls=DjangoJSONEncoder)
            return format_html("<pre>{}</pre>", pretty_value)
        except json.JSONDecodeError:
            return format_html("<pre>{}</pre>", obj.value)

    value_display.short_description = "Value"

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return self.readonly_fields + ("value_type", "is_public")
        return self.readonly_fields


class ExportActionMixin:
    model = None

    def export_csv(self, request, queryset) -> HttpResponse:
        opts = self.model._meta
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = "attachment; filename={}.csv".format(
            opts.verbose_name
        )
        writer = csv.writer(response)
        fields = [
            field
            for field in opts.get_fields()
            if not field.many_to_many and not field.one_to_many
        ]
        # Write a first row with header information
        writer.writerow([field.verbose_name for field in fields])
        # Write data rows
        for obj in queryset:
            data_row = []
            for field in fields:
                value = getattr(obj, field.name)
                data_row.append(value)
            writer.writerow(data_row)
        return response

    export_csv.short_description = "Export selected to CSV"

    def export_xml(self, request, queryset) -> HttpResponse:
        opts = self.model._meta
        fields = [
            field
            for field in opts.get_fields()
            if not field.many_to_many and not field.one_to_many
        ]

        root = ET.Element("{}s".format(opts.verbose_name))
        for obj in queryset:
            obj_element = ET.SubElement(root, opts.verbose_name)
            for field in fields:
                field_element = ET.SubElement(obj_element, field.name)
                field_element.text = str(getattr(obj, field.name))

        xml_string = ET.tostring(root, encoding="utf-8").decode("utf-8")
        response = HttpResponse(xml_string, content_type="text/xml")
        response["Content-Disposition"] = "attachment; filename={}.xml".format(
            opts.verbose_name
        )
        return response

    export_xml.short_description = "Export selected to XML"

    def get_export_formats(self) -> list[dict[str, str]]:
        return [
            {"format": "csv", "label": "CSV"},
            {"format": "pdf", "label": "PDF"},
            {"format": "xml", "label": "XML"},
        ]


class ExportModelAdmin(ExportActionMixin, admin.ModelAdmin):
    def get_export_formats(self) -> list[dict[str, str]]:
        return super().get_export_formats()

    def get_export_fields(self) -> list[dict[str, str]]:
        fields = []
        opts = self.model.meta

        for field in opts.get_fields():
            if not field.many_to_many and not field.one_to_many:
                fields.append({"name": field.name, "label": field.verbose_name})
            return fields
