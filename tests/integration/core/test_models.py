import json

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase

from core.enum import SettingsValueTypeEnum
from core.models import Settings


class SettingsModelTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_create_setting_with_valid_key(self):
        setting = Settings.objects.create(
            key="APP_VALID_KEY",
            value="Test value",
            value_type=SettingsValueTypeEnum.STRING,
        )
        self.assertIsNotNone(setting.pk)

    def test_set_and_get_value_for_different_types(self):
        types_and_values = [
            ("str", "example string"),
            ("int", 42),
            ("bool", True),
            ("dict", {"key": "value"}),
            ("list", [1, 2, 3]),
            ("float", 3.14),
        ]
        for python_type, val in types_and_values:
            setting = Settings(key=f"APP_{python_type.upper()}_KEY")
            setting.set_value(val)
            setting.save()

            retrieved_value = setting.get_value()
            self.assertEqual(retrieved_value, val)

    def test_create_setting_with_invalid_key_prefix(self):
        with self.assertRaises(ValidationError):
            Settings.objects.create(
                key="INVALID_KEY",
                value="Test value",
                value_type=SettingsValueTypeEnum.STRING,
            )

    def test_setting_value_type_integrity(self):
        setting = Settings.objects.create(
            key="APP_INTEGER",
            value=json.dumps(10),
            value_type=SettingsValueTypeEnum.INTEGER,
        )
        self.assertEqual(setting.get_value(), 10)

    def test_boolean_value_setting_and_retrieval(self):
        setting = Settings(key="APP_BOOLEAN", value_type=SettingsValueTypeEnum.BOOLEAN)
        setting.set_value(True)
        setting.save()

        retrieved_setting = Settings.objects.get(key="APP_BOOLEAN")
        self.assertTrue(retrieved_setting.get_value())

    def test_setting_validation_for_incorrect_type(self):
        setting = Settings(
            key="APP_INVALID_TYPE",
            value=json.dumps("not an integer"),
            value_type=SettingsValueTypeEnum.INTEGER,
        )
        with self.assertRaises(ValidationError):
            setting.save()

    def test_get_and_set_class_methods(self):
        Settings.set_setting("APP_TEST_SETTING", "test value")
        value = Settings.get_setting("APP_TEST_SETTING")
        self.assertEqual(value, "test value")

    def test_get_setting_with_default(self):
        default_value = "default"
        value = Settings.get_setting("APP_NON_EXISTENT", default=default_value)
        self.assertEqual(value, default_value)

    def test_caching_logic(self):
        setting = Settings(
            key="APP_TEST_CACHING",
            value=json.dumps("cached value"),
            value_type=SettingsValueTypeEnum.STRING,
        )
        setting.save()

        cached_value = cache.get("APP_TEST_CACHING")
        self.assertIsNone(cached_value)

        value_from_method = Settings.get_setting("APP_TEST_CACHING")
        self.assertEqual(value_from_method, "cached value")

        cached_value = cache.get("APP_TEST_CACHING")
        self.assertIsNotNone(cached_value)

    def test_setting_not_found_returns_default(self):
        default_val = "default"
        value = Settings.get_setting("NON_EXISTENT_KEY", default=default_val)
        self.assertEqual(value, default_val)

    def tearDown(self):
        cache.clear()
