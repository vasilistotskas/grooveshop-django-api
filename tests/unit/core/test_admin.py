import csv
import xml.etree.ElementTree as ET
from io import StringIO
from unittest.mock import MagicMock

from django.contrib.messages.storage.fallback import FallbackStorage
from django.db import models
from django.db.models.options import Options
from django.http import HttpRequest
from django.test import TestCase, override_settings
from djmoney.models.fields import Money
from parler.models import TranslatableModel

from core.admin import ExportActionMixin


class MockAdminModel(models.Model):
    name = models.CharField(max_length=100)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    description = models.TextField()

    class Meta:
        app_label = "test_app"
        verbose_name = "Mock Admin Model"

    def __str__(self):
        return self.name


class MockTranslatableModel(TranslatableModel):
    code = models.CharField(max_length=50)

    class Meta:
        app_label = "test_app"
        verbose_name = "test_translatable_model"

    class TranslatableMeta:
        fields = ["title", "description"]


class MockParlerMeta:
    @staticmethod
    def get_translated_fields():
        return ["title", "description"]


class ExportActionMixinTest(TestCase):
    def setUp(self):
        self.mixin = ExportActionMixin()
        self.mixin.model = MockAdminModel

        self.request = HttpRequest()
        self.request.session = "session"
        messages = FallbackStorage(self.request)
        self.request._messages = messages

        self.queryset = MagicMock()

        self.test_model_instance = MagicMock(spec=MockAdminModel)
        self.test_model_instance.name = "Test Model"
        self.test_model_instance.price = Money(amount=10.50, currency="USD")
        self.test_model_instance.description = "Test description"

        MockTranslatableModel._parler_meta = MockParlerMeta()
        self.translatable_model_instance = MagicMock(spec=MockTranslatableModel)
        self.translatable_model_instance.code = "test-code"
        self.en_translation = MagicMock()
        self.en_translation.title = "English Title"
        self.en_translation.description = "English Description"
        self.fr_translation = MagicMock()
        self.fr_translation.title = "French Title"
        self.fr_translation.description = "French Description"

        def mock_get_translation(lang_code):
            if lang_code == "en":
                return self.en_translation
            elif lang_code == "fr":
                return self.fr_translation
            raise models.ObjectDoesNotExist

        self.translatable_model_instance.get_translation = mock_get_translation

    def test_get_exportable_fields(self):
        mock_opts = MagicMock(spec=Options)

        regular_field = MagicMock()
        regular_field.many_to_many = False
        regular_field.one_to_many = False
        regular_field.one_to_one = False

        m2m_field = MagicMock()
        m2m_field.many_to_many = True
        m2m_field.one_to_many = False
        m2m_field.one_to_one = False

        o2m_field = MagicMock()
        o2m_field.many_to_many = False
        o2m_field.one_to_many = True
        o2m_field.one_to_one = False

        o2o_field = MagicMock()
        o2o_field.many_to_many = False
        o2o_field.one_to_many = False
        o2o_field.one_to_one = True

        mock_opts.get_fields.return_value = [
            regular_field,
            m2m_field,
            o2m_field,
            o2o_field,
        ]

        fields = self.mixin._get_exportable_fields(mock_opts)

        self.assertEqual(len(fields), 1)
        self.assertEqual(fields[0], regular_field)

    @override_settings(LANGUAGES=[("en", "English"), ("fr", "French")])
    def test_export_csv_standard_model(self):
        self.mixin.model._meta = MagicMock()
        self.mixin.model._meta.verbose_name = "test_model"

        name_field = MagicMock()
        name_field.name = "name"
        name_field.verbose_name = "Name"
        name_field.many_to_many = False
        name_field.one_to_many = False
        name_field.one_to_one = False

        price_field = MagicMock()
        price_field.name = "price"
        price_field.verbose_name = "Price"
        price_field.many_to_many = False
        price_field.one_to_many = False
        price_field.one_to_one = False

        desc_field = MagicMock()
        desc_field.name = "description"
        desc_field.verbose_name = "Description"
        desc_field.many_to_many = False
        desc_field.one_to_many = False
        desc_field.one_to_one = False

        self.mixin.model._meta.get_fields.return_value = [
            name_field,
            price_field,
            desc_field,
        ]

        self.queryset.__iter__.return_value = [self.test_model_instance]

        response = self.mixin.export_csv(self.request, self.queryset)

        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertEqual(
            response["Content-Disposition"],
            "attachment; filename=test_model.csv",
        )

        content = response.content.decode("utf-8")
        reader = csv.reader(StringIO(content))
        rows = list(reader)

        self.assertEqual(rows[0], ["Name", "Price", "Description"])

        self.assertEqual(rows[1][0], "Test Model")
        self.assertEqual(rows[1][1], "10.5")
        self.assertEqual(rows[1][2], "Test description")

    @override_settings(LANGUAGES=[("en", "English"), ("fr", "French")])
    def test_export_csv_translatable_model(self):
        self.mixin.model = MockTranslatableModel
        self.mixin.model._meta = MagicMock()
        self.mixin.model._meta.verbose_name = "test_translatable_model"

        code_field = MagicMock()
        code_field.name = "code"
        code_field.verbose_name = "Code"
        code_field.many_to_many = False
        code_field.one_to_many = False
        code_field.one_to_one = False

        self.mixin.model._meta.get_fields.return_value = [code_field]

        self.queryset.__iter__.return_value = [self.translatable_model_instance]

        response = self.mixin.export_csv(self.request, self.queryset)

        content = response.content.decode("utf-8")
        reader = csv.reader(StringIO(content))
        rows = list(reader)

        expected_headers = [
            "Code",
            "title_en",
            "description_en",
            "title_fr",
            "description_fr",
        ]
        self.assertEqual(rows[0], expected_headers)

        self.assertEqual(rows[1][0], "test-code")
        self.assertEqual(rows[1][1], "English Title")
        self.assertEqual(rows[1][2], "English Description")
        self.assertEqual(rows[1][3], "French Title")
        self.assertEqual(rows[1][4], "French Description")

    def test_add_base_fields_to_xml(self):
        root = ET.Element("root")
        obj_element = ET.SubElement(root, "test_model")

        name_field = MagicMock()
        name_field.name = "name"

        price_field = MagicMock()
        price_field.name = "price"

        json_field = MagicMock(spec=models.JSONField)
        json_field.name = "json_data"
        self.test_model_instance.json_data = {"key": "value"}

        self.mixin._add_base_fields_to_xml(
            self.test_model_instance,
            obj_element,
            [name_field, price_field, json_field],
        )

        self.assertEqual(obj_element.find("name").text, "Test Model")
        self.assertEqual(obj_element.find("price").text, "10.5")
        self.assertEqual(obj_element.find("json_data").text, '{"key": "value"}')

    @override_settings(LANGUAGES=[("en", "English"), ("fr", "French")])
    def test_add_translated_fields_to_xml(self):
        root = ET.Element("root")
        obj_element = ET.SubElement(root, "test_translatable_model")

        self.mixin._add_translated_fields_to_xml(
            self.translatable_model_instance,
            obj_element,
            ["title", "description"],
        )

        translations = obj_element.find("translations")
        self.assertIsNotNone(translations)

        en_trans = translations.find("./translation[@lang='en']")
        self.assertIsNotNone(en_trans)
        self.assertEqual(en_trans.find("title").text, "English Title")
        self.assertEqual(
            en_trans.find("description").text, "English Description"
        )

        fr_trans = translations.find("./translation[@lang='fr']")
        self.assertIsNotNone(fr_trans)
        self.assertEqual(fr_trans.find("title").text, "French Title")
        self.assertEqual(
            fr_trans.find("description").text, "French Description"
        )

    def test_generate_xml_response(self):
        root = ET.Element("test_models")
        model = ET.SubElement(root, "test_model")
        name = ET.SubElement(model, "name")
        name.text = "Test Model"

        response = self.mixin._generate_xml_response(root, "test_model")

        self.assertEqual(
            response["Content-Type"], "application/xml; charset=utf-8"
        )
        self.assertEqual(
            response["Content-Disposition"],
            "attachment; filename=test_model.xml",
        )

        content = response.content.decode("utf-8")
        self.assertTrue(
            content.startswith('<?xml version="1.0" encoding="UTF-8"?>')
        )

        parsed_root = ET.fromstring(content)
        self.assertEqual(parsed_root.tag, "test_models")
        self.assertEqual(parsed_root[0].tag, "test_model")
        self.assertEqual(parsed_root[0][0].tag, "name")
        self.assertEqual(parsed_root[0][0].text, "Test Model")

    def test_generate_xml_response_fallback(self):
        root = ET.Element("root")
        test_element = ET.SubElement(root, "test")
        test_element.text = "test content"

        response = self.mixin._generate_xml_response(root, "test")

        self.assertEqual(
            response["Content-Type"], "application/xml; charset=utf-8"
        )
        self.assertEqual(
            response["Content-Disposition"], "attachment; filename=test.xml"
        )
        self.assertIn("test content", response.content.decode("utf-8"))

    @override_settings(LANGUAGES=[("en", "English"), ("fr", "French")])
    def test_export_xml_standard_model(self):
        self.mixin.model._meta = MagicMock()
        self.mixin.model._meta.verbose_name = "test_model"

        name_field = MagicMock()
        name_field.name = "name"
        name_field.verbose_name = "Name"
        name_field.many_to_many = False
        name_field.one_to_many = False
        name_field.one_to_one = False

        self.mixin.model._meta.get_fields.return_value = [name_field]

        self.queryset.__iter__.return_value = [self.test_model_instance]
        self.queryset.__bool__.return_value = True

        response = self.mixin.export_xml(self.request, self.queryset)

        self.assertEqual(
            response["Content-Type"], "application/xml; charset=utf-8"
        )
        self.assertEqual(
            response["Content-Disposition"],
            "attachment; filename=test_model.xml",
        )

        content = response.content.decode("utf-8")
        self.assertTrue(
            content.startswith('<?xml version="1.0" encoding="UTF-8"?>')
        )

        parsed_root = ET.fromstring(content)
        self.assertEqual(parsed_root.tag, "test_models")
        self.assertEqual(parsed_root[0].tag, "test_model")
        self.assertEqual(parsed_root[0][0].tag, "name")
        self.assertEqual(parsed_root[0][0].text, "Test Model")

    @override_settings(LANGUAGES=[("en", "English"), ("fr", "French")])
    def test_export_xml_translatable_model(self):
        self.mixin.model = MockTranslatableModel
        self.mixin.model._meta = MagicMock()
        self.mixin.model._meta.verbose_name = "test_translatable_model"

        code_field = MagicMock()
        code_field.name = "code"
        code_field.verbose_name = "Code"
        code_field.many_to_many = False
        code_field.one_to_many = False
        code_field.one_to_one = False

        self.mixin.model._meta.get_fields.return_value = [code_field]

        self.queryset.__iter__.return_value = [self.translatable_model_instance]
        self.queryset.__bool__.return_value = True

        response = self.mixin.export_xml(self.request, self.queryset)

        self.assertEqual(
            response["Content-Type"], "application/xml; charset=utf-8"
        )

        content = response.content.decode("utf-8")
        parsed_root = ET.fromstring(content)

        model_element = parsed_root.find("./test_translatable_model")
        self.assertIsNotNone(model_element)
        self.assertEqual(model_element.find("./code").text, "test-code")

        translations = model_element.find("./translations")
        self.assertIsNotNone(translations)

        en_trans = translations.find("./translation[@lang='en']")
        self.assertIsNotNone(en_trans)
        self.assertEqual(en_trans.find("./title").text, "English Title")

    def test_export_xml_empty_queryset(self):
        self.queryset.__bool__.return_value = False

        response = self.mixin.export_xml(self.request, self.queryset)

        self.assertEqual(response.status_code, 400)

    def test_export_xml_error_handling(self):
        self.mixin.model._meta = MagicMock()
        self.mixin.model._meta.verbose_name = "test_model"
        self.mixin.model._meta.get_fields.return_value = []

        self.queryset.__iter__.return_value = []

        response = self.mixin.export_xml(self.request, self.queryset)

        self.assertEqual(
            response["Content-Type"], "application/xml; charset=utf-8"
        )
        self.assertEqual(
            response["Content-Disposition"],
            "attachment; filename=test_model.xml",
        )

    def test_get_export_formats(self):
        formats = self.mixin.get_export_formats()

        self.assertEqual(len(formats), 2)
        self.assertEqual(formats[0]["format"], "csv")
        self.assertEqual(formats[0]["label"], "CSV")
        self.assertEqual(formats[1]["format"], "xml")
        self.assertEqual(formats[1]["label"], "XML")
