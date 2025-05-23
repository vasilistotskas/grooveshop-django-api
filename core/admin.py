import csv
import json
import logging
import traceback
import xml.dom.minidom
import xml.etree.ElementTree as ET
from xml.etree.ElementTree import (
    ParseError as ETParseError,
)

from django.conf import settings
from django.contrib import admin, messages
from django.contrib.contenttypes.fields import GenericRelation
from django.db import models
from django.db.models import QuerySet
from django.db.models.options import Options
from django.http import HttpRequest, HttpResponse
from django.utils.translation import gettext_lazy as _
from django_celery_beat.admin import (
    ClockedScheduleAdmin as BaseClockedScheduleAdmin,
)
from django_celery_beat.admin import (
    CrontabScheduleAdmin as BaseCrontabScheduleAdmin,
)
from django_celery_beat.admin import PeriodicTaskAdmin as BasePeriodicTaskAdmin
from django_celery_beat.admin import PeriodicTaskForm, TaskSelectWidget
from django_celery_beat.models import (
    ClockedSchedule,
    CrontabSchedule,
    IntervalSchedule,
    PeriodicTask,
    SolarSchedule,
)
from djmoney.models.fields import Money
from mptt.fields import TreeForeignKey
from parler.models import TranslatableModel
from unfold.admin import ModelAdmin
from unfold.widgets import UnfoldAdminSelectWidget, UnfoldAdminTextInputWidget

logger = logging.getLogger(__name__)

admin.site.unregister(PeriodicTask)
admin.site.unregister(IntervalSchedule)
admin.site.unregister(CrontabSchedule)
admin.site.unregister(SolarSchedule)
admin.site.unregister(ClockedSchedule)


class ExportActionMixin:
    model = None

    def _get_exportable_fields(self, opts: Options) -> list:
        return [
            field
            for field in opts.get_fields()
            if not field.many_to_many
            and not field.one_to_many
            and not field.one_to_one
        ]

    def export_csv(self, request: HttpRequest, queryset: QuerySet):
        opts = self.model._meta
        model_verbose_name_str = str(opts.verbose_name)
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = (
            f"attachment; filename={model_verbose_name_str}.csv"
        )
        writer = csv.writer(response)

        base_fields = self._get_exportable_fields(opts)
        header = [str(field.verbose_name) for field in base_fields]

        translated_field_names = []
        is_translatable = issubclass(self.model, TranslatableModel) and hasattr(
            self.model, "_parler_meta"
        )
        if is_translatable:
            translated_field_names = list(
                self.model._parler_meta.get_translated_fields()
            )
            for lang_code, _lang_name in settings.LANGUAGES:
                for field_name in translated_field_names:
                    header.append(f"{field_name}_{lang_code}")

        writer.writerow(header)

        for obj in queryset:
            data_row = []
            for field in base_fields:
                value = getattr(obj, field.name, None)
                if isinstance(value, Money):
                    value = value.amount
                elif isinstance(value, models.Model):
                    value = str(value)
                elif isinstance(value, dict | list):
                    value = json.dumps(value)
                elif value is None:
                    value = ""
                data_row.append(str(value))

            if is_translatable and translated_field_names:
                for lang_code, _lang_name in settings.LANGUAGES:
                    try:
                        translation = obj.get_translation(lang_code)
                    except models.ObjectDoesNotExist:
                        translation = None

                    for field_name in translated_field_names:
                        value = (
                            getattr(translation, field_name, "")
                            if translation
                            else ""
                        )
                        data_row.append(str(value) if value is not None else "")

            writer.writerow(data_row)

        messages.success(request, _("CSV export successful."))
        return response

    def _add_base_fields_to_xml(
        self, obj: models.Model, obj_element: ET.Element, base_fields: list
    ):
        for field in base_fields:
            field_tag_name = str(field.name)
            field_element = ET.SubElement(obj_element, field_tag_name)
            value = getattr(obj, field.name, None)

            if value is None:
                field_element.text = ""
            elif isinstance(
                field, models.ForeignKey | models.OneToOneField | TreeForeignKey
            ):
                field_element.text = str(value) if value else ""
            elif isinstance(value, Money):
                field_element.text = str(value.amount)
            elif isinstance(field, models.JSONField):
                field_element.text = json.dumps(value) if value else ""
            elif isinstance(field, GenericRelation):
                field_element.text = f"[GenericRelation: {field.name}]"
            elif hasattr(value, "__str__"):
                field_element.text = str(value)
            else:
                try:
                    field_element.text = str(value)
                except Exception as e:
                    logger.error(
                        f"Error converting base field {field.name} to string: {e}"
                    )
                    field_element.text = (
                        f"[Unsupported type: {type(value).__name__}]"
                    )

    def _add_translated_fields_to_xml(
        self,
        obj: models.Model,
        obj_element: ET.Element,
        translated_field_names: list,
    ):
        translations_element = ET.SubElement(obj_element, "translations")
        for lang_code, _lang_name in settings.LANGUAGES:
            try:
                translation_obj = obj.get_translation(lang_code)
                if not translation_obj:
                    continue

                translation_element = ET.SubElement(
                    translations_element,
                    "translation",
                    attrib={"lang": lang_code},
                )
                for t_field_name in translated_field_names:
                    t_value = getattr(translation_obj, t_field_name, None)
                    t_field_element = ET.SubElement(
                        translation_element, t_field_name
                    )

                    if isinstance(t_value, str) and (
                        "<" in t_value or ">" in t_value
                    ):
                        t_field_element.text = None
                        try:
                            cdata = ET.CDATA(str(t_value))
                            t_field_element.append(cdata)
                        except (
                            ETParseError,
                            TypeError,
                            ValueError,
                        ) as markup_err:
                            logger.warning(
                                f"Field {t_field_name} for {obj} lang {lang_code} contains invalid markup or cannot create CDATA ({markup_err}), exporting as plain string."
                            )
                            t_field_element.text = (
                                str(t_value) if t_value is not None else ""
                            )
                        except Exception as cdata_err:
                            logger.error(
                                f"Unexpected error creating CDATA for {t_field_name} ({obj}, {lang_code}): {cdata_err}"
                            )
                            t_field_element.text = (
                                str(t_value) if t_value is not None else ""
                            )
                    else:
                        t_field_element.text = (
                            str(t_value) if t_value is not None else ""
                        )

            except models.ObjectDoesNotExist:
                continue
            except AttributeError as e:
                logger.error(
                    f"AttributeError getting translation for {obj}: {e}"
                )
                break

    def _generate_xml_response(self, root, filename_base):
        xml_declaration = '<?xml version="1.0" encoding="UTF-8"?>\n'
        try:
            rough_string = ET.tostring(root, "utf-8")
            reparsed = xml.dom.minidom.parseString(rough_string)
            pretty_xml_string = reparsed.toprettyxml(
                indent="  ", encoding="utf-8"
            ).decode("utf-8")

            if pretty_xml_string.strip().startswith("<?xml"):
                pretty_xml_string = pretty_xml_string.split("\n", 1)[1]

            final_xml_string = "\n".join(
                line for line in pretty_xml_string.split("\n") if line.strip()
            )
            final_xml_string = xml_declaration + final_xml_string

            response = HttpResponse(
                final_xml_string, content_type="application/xml; charset=utf-8"
            )

        except Exception as pretty_print_err:
            logger.warning(
                f"Could not pretty-print XML, falling back to raw string: {pretty_print_err}"
            )
            try:
                xml_string = ET.tostring(
                    root, encoding="utf-8", method="xml"
                ).decode("utf-8")
                response = HttpResponse(
                    xml_declaration + xml_string,
                    content_type="application/xml; charset=utf-8",
                )
            except Exception as raw_xml_err:
                logger.error(
                    f"Failed even generating raw XML string: {raw_xml_err}"
                )
                raise

        response["Content-Disposition"] = (
            f"attachment; filename={filename_base}.xml"
        )
        return response

    def export_xml(self, request: HttpRequest, queryset: QuerySet):
        try:
            opts = self.model._meta
            model_verbose_name_str = str(opts.verbose_name)
            root_tag_name = model_verbose_name_str + "s"

            base_fields = self._get_exportable_fields(opts)

            translated_field_names = []
            is_translatable = issubclass(
                self.model, TranslatableModel
            ) and hasattr(self.model, "_parler_meta")
            if is_translatable:
                translated_field_names = list(
                    self.model._parler_meta.get_translated_fields()
                )

            if not queryset:
                messages.warning(request, _("No items selected for export."))
                return HttpResponse(status=400)

            root = ET.Element(root_tag_name)

            for obj in queryset:
                obj_element = ET.SubElement(root, model_verbose_name_str)
                self._add_base_fields_to_xml(obj, obj_element, base_fields)
                if is_translatable and translated_field_names:
                    self._add_translated_fields_to_xml(
                        obj, obj_element, translated_field_names
                    )

            response = self._generate_xml_response(root, model_verbose_name_str)

            messages.success(request, _("XML export successful."))
            return response

        except Exception as e:
            error_details = traceback.format_exc()
            error_message = _(
                "An unexpected error occurred during XML export: %s"
            ) % str(e)
            messages.error(request, error_message)
            logger.error(f"XML Export Error: {e}\n{error_details}")
            return HttpResponse(
                "Error generating XML file. Please contact support.",
                status=500,
                content_type="text/plain",
            )

    def get_export_formats(self) -> list:
        return [
            {"format": "csv", "label": "CSV"},
            {"format": "xml", "label": "XML"},
        ]


class ExportModelAdmin(ExportActionMixin, ModelAdmin):
    actions = [
        "export_csv",
        "export_xml",
    ]


class UnfoldTaskSelectWidget(UnfoldAdminSelectWidget, TaskSelectWidget):
    pass


class UnfoldPeriodicTaskForm(PeriodicTaskForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["task"].widget = UnfoldAdminTextInputWidget()
        self.fields["regtask"].widget = UnfoldTaskSelectWidget()


@admin.register(PeriodicTask)
class PeriodicTaskAdmin(BasePeriodicTaskAdmin, ModelAdmin):
    form = UnfoldPeriodicTaskForm


@admin.register(IntervalSchedule)
class IntervalScheduleAdmin(ModelAdmin):
    pass


@admin.register(CrontabSchedule)
class CrontabScheduleAdmin(BaseCrontabScheduleAdmin, ModelAdmin):
    pass


@admin.register(SolarSchedule)
class SolarScheduleAdmin(ModelAdmin):
    pass


@admin.register(ClockedSchedule)
class ClockedScheduleAdmin(BaseClockedScheduleAdmin, ModelAdmin):
    pass
