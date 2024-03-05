from django.conf import settings
from django.test import TestCase

from blog.models.post import BlogPost
from core.api.schema import generate_schema_multi_lang


languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE


class GenerateSchemaMultiLangTest(TestCase):
    def test_generate_schema_empty(self):
        instance = BlogPost()

        schema = generate_schema_multi_lang(instance)

        expected_schema = {
            "type": "object",
            "properties": {},
        }

        for lang in languages:
            expected_schema["properties"][lang] = {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "subtitle": {"type": "string"},
                    "body": {"type": "string"},
                },
            }

        self.assertEqual(schema, expected_schema)

    def test_generate_schema_with_translations(self):
        instance = BlogPost()
        instance.title = "Title"
        instance.save()

        schema = generate_schema_multi_lang(instance)

        expected_schema = {
            "type": "object",
            "properties": {},
        }

        for lang in languages:
            expected_schema["properties"][lang] = {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "subtitle": {"type": "string"},
                    "body": {"type": "string"},
                },
            }

        self.assertEqual(schema, expected_schema)

    def test_generate_schema_no_languages(self):
        original_languages = settings.PARLER_LANGUAGES[settings.SITE_ID]
        settings.PARLER_LANGUAGES[settings.SITE_ID] = []

        instance = BlogPost()
        instance.title = "Title"
        instance.save()

        schema = generate_schema_multi_lang(instance)

        expected_schema = {
            "type": "object",
            "properties": {},
        }

        self.assertEqual(schema, expected_schema)

        settings.PARLER_LANGUAGES[settings.SITE_ID] = original_languages

    def tearDown(self) -> None:
        super().tearDown()
        BlogPost.objects.all().delete()
