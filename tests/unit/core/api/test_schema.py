import copy

from django.conf import settings
from django.test import TestCase, override_settings

from blog.models.category import BlogCategory
from core.api.schema import generate_schema_multi_lang


class GenerateSchemaMultiLangTest(TestCase):
    def _get_languages(self):
        return [
            lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
        ]

    def test_generate_schema_empty(self):
        instance = BlogCategory()

        schema = generate_schema_multi_lang(instance)

        expected_schema = {
            "type": "object",
            "properties": {},
        }

        for lang in self._get_languages():
            expected_schema["properties"][lang] = {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                },
            }

        self.assertEqual(schema, expected_schema)

    def test_generate_schema_with_translations(self):
        instance = BlogCategory()
        instance.name = "name"
        instance.save()

        schema = generate_schema_multi_lang(instance)

        expected_schema = {
            "type": "object",
            "properties": {},
        }

        for lang in self._get_languages():
            expected_schema["properties"][lang] = {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                },
            }

        self.assertEqual(schema, expected_schema)

    def test_generate_schema_no_languages(self):
        patched = copy.deepcopy(settings.PARLER_LANGUAGES)
        patched[settings.SITE_ID] = []

        with override_settings(PARLER_LANGUAGES=patched):
            instance = BlogCategory()
            instance.name = "name"
            instance.save()

            schema = generate_schema_multi_lang(instance)

            expected_schema = {
                "type": "object",
                "properties": {},
            }

            self.assertEqual(schema, expected_schema)
