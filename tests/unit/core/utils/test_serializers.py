import json
from unittest.mock import Mock

import pytest
from django.db import models
from django.test import TestCase
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIRequestFactory
from rest_framework.viewsets import ModelViewSet

from core.utils.serializers import (
    ActionConfig,
    SerializersConfig,
    TranslatedFieldExtended,
    create_schema_view_config,
    crud_config,
)


class MockSerializerModel(models.Model):
    field1 = models.IntegerField()
    field2 = models.CharField(max_length=100)
    field3 = models.IntegerField()
    field4 = models.IntegerField()

    class Meta:
        app_label = "test_app"
        verbose_name = "Mock Serializer Model"
        verbose_name_plural = "Mock Serializer Models"

    def __str__(self):
        return self.field2


class DummySerializer(serializers.Serializer):
    field1 = serializers.CharField()
    field2 = serializers.IntegerField()


class AnotherDummySerializer(serializers.Serializer):
    field3 = serializers.CharField()
    field4 = serializers.BooleanField()


class DummyTestSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField(max_length=100)
    description = serializers.CharField(required=False)


class DummyTestDetailSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField(max_length=100)
    description = serializers.CharField()
    created_at = serializers.DateTimeField()


class DummyTestWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    description = serializers.CharField(required=False)


class DummyTestViewSet(ModelViewSet):
    serializers_config: SerializersConfig = {
        **crud_config(
            list=DummyTestSerializer,
            detail=DummyTestDetailSerializer,
            write=DummyTestWriteSerializer,
        ),
    }


class DummyTestViewSetWithSerializerClass(ModelViewSet):
    serializer_class = DummyTestSerializer


class DummyTestViewSetMissingSerializers(ModelViewSet):
    pass


@pytest.fixture
def request_factory():
    return APIRequestFactory()


@pytest.fixture
def mock_request():
    request = Mock()
    request.user = Mock()
    request.query_params = {}
    return request


class TestTranslatedFieldExtended(TestCase):
    def test_to_internal_value_with_valid_data(self):
        # ``el`` and ``en``, not the ``fr`` this used to send: the
        # deployment serves el/en/de, so ``fr`` was only ever accepted
        # because the keys went unchecked.
        data = {
            "en": {"field1": "value1", "field2": 123},
            "el": {"field1": "τιμή1", "field2": 456},
        }
        field = TranslatedFieldExtended(serializer_class=DummySerializer)
        result = field.to_internal_value(json.dumps(data))
        expected_result = {
            "en": {"field1": "value1", "field2": 123},
            "el": {"field1": "τιμή1", "field2": 456},
        }
        self.assertEqual(result, expected_result)

    def test_an_unsupported_language_is_a_validation_error(self):
        field = TranslatedFieldExtended(serializer_class=DummySerializer)

        with self.assertRaises(ValidationError) as ctx:
            field.to_internal_value(
                json.dumps({"fr": {"field1": "valeur1", "field2": 456}})
            )

        self.assertIn("fr", ctx.exception.detail)

    def test_a_multipart_string_that_is_not_json_is_a_validation_error(self):
        """It raised ``json.JSONDecodeError``, i.e. HTTP 500."""
        field = TranslatedFieldExtended(serializer_class=DummySerializer)

        with self.assertRaises(ValidationError):
            field.to_internal_value("not-json{")


class TestCreateSchemaViewConfig(TestCase):
    def test_basic_config_generation(self):
        serializers_config: SerializersConfig = {
            **crud_config(
                list=DummyTestSerializer,
                detail=DummyTestDetailSerializer,
                write=DummyTestWriteSerializer,
            ),
        }

        config = create_schema_view_config(
            model_class=MockSerializerModel,
            display_config={
                "tag": "Test Models",
            },
            serializers_config=serializers_config,
        )

        assert "list" in config
        assert "create" in config
        assert "retrieve" in config
        assert "update" in config
        assert "partial_update" in config
        assert "destroy" in config

        for decorator in config.values():
            assert callable(decorator)
            assert hasattr(decorator, "__name__")

    def test_config_with_error_serializer(self):
        serializers_config: SerializersConfig = {
            **crud_config(
                list=DummyTestSerializer,
                detail=DummyTestDetailSerializer,
                write=DummyTestWriteSerializer,
            ),
        }

        config = create_schema_view_config(
            model_class=MockSerializerModel,
            display_config={
                "tag": "Test Models",
            },
            serializers_config=serializers_config,
            error_serializer=DummyTestSerializer,
        )

        expected_operations = [
            "list",
            "create",
            "retrieve",
            "update",
            "partial_update",
            "destroy",
        ]
        for operation in expected_operations:
            assert operation in config
            assert callable(config[operation])

    def test_config_with_custom_actions(self):
        serializers_config: SerializersConfig = {
            **crud_config(
                list=DummyTestSerializer,
                detail=DummyTestDetailSerializer,
                write=DummyTestWriteSerializer,
            ),
            "custom_action": ActionConfig(
                response=DummyTestSerializer,
                operation_id="customAction",
                summary="Custom action",
                tags=["Custom"],
            ),
        }

        config = create_schema_view_config(
            model_class=MockSerializerModel,
            display_config={
                "tag": "Test Models",
            },
            serializers_config=serializers_config,
        )

        crud_operations = [
            "list",
            "create",
            "retrieve",
            "update",
            "partial_update",
            "destroy",
        ]
        for operation in crud_operations:
            assert operation in config
            assert callable(config[operation])

        assert "custom_action" in config
        assert callable(config["custom_action"])

    def test_config_function_returns_decorator(self):
        serializers_config: SerializersConfig = {
            **crud_config(
                list=DummyTestSerializer,
                detail=DummyTestDetailSerializer,
                write=DummyTestWriteSerializer,
            ),
        }

        config = create_schema_view_config(
            model_class=MockSerializerModel,
            display_config={
                "tag": "Test Models",
            },
            serializers_config=serializers_config,
        )

        @config["list"]
        def dummy_list_view():
            pass

        @config["create"]
        def dummy_create_view():
            pass

        assert callable(dummy_list_view)
        assert callable(dummy_create_view)
