import json
from unittest.mock import Mock

import pytest
from django.db import models
from django.test import TestCase
from rest_framework import serializers
from rest_framework.test import APIRequestFactory
from rest_framework.viewsets import ModelViewSet
from core.utils.serializers import (
    TranslatedFieldExtended,
    create_schema_view_config,
    RequestSerializersConfig,
    ResponseSerializersConfig,
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
    response_serializers = {
        "create": DummyTestDetailSerializer,
        "update": DummyTestDetailSerializer,
        "partial_update": DummyTestDetailSerializer,
    }
    request_serializers = {
        "create": DummyTestWriteSerializer,
        "update": DummyTestWriteSerializer,
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
        data = {
            "en": {"field1": "value1", "field2": 123},
            "fr": {"field1": "valeur1", "field2": 456},
        }
        field = TranslatedFieldExtended(serializer_class=DummySerializer)
        result = field.to_internal_value(json.dumps(data))
        expected_result = {
            "en": {"field1": "value1", "field2": 123},
            "fr": {"field1": "valeur1", "field2": 456},
        }
        self.assertEqual(result, expected_result)


class TestCreateSchemaViewConfig(TestCase):
    def test_basic_config_generation(self):
        req_serializers: RequestSerializersConfig = {
            "create": DummyTestWriteSerializer,
            "update": DummyTestWriteSerializer,
            "partial_update": DummyTestWriteSerializer,
        }

        res_serializers: ResponseSerializersConfig = {
            "create": DummyTestDetailSerializer,
            "list": DummyTestSerializer,
            "retrieve": DummyTestDetailSerializer,
            "update": DummyTestDetailSerializer,
            "partial_update": DummyTestDetailSerializer,
        }

        config = create_schema_view_config(
            model_class=MockSerializerModel,
            display_config={
                "tag": "Test Models",
            },
            request_serializers=req_serializers,
            response_serializers=res_serializers,
        )

        assert "list" in config
        assert "create" in config
        assert "retrieve" in config
        assert "update" in config
        assert "partial_update" in config
        assert "destroy" in config

        for _operation_name, decorator in config.items():
            assert callable(decorator)
            assert hasattr(decorator, "__name__")

    def test_config_with_error_serializer(self):
        req_serializers: RequestSerializersConfig = {
            "create": DummyTestWriteSerializer,
            "update": DummyTestWriteSerializer,
            "partial_update": DummyTestWriteSerializer,
        }

        res_serializers: ResponseSerializersConfig = {
            "create": DummyTestDetailSerializer,
            "list": DummyTestSerializer,
            "retrieve": DummyTestDetailSerializer,
            "update": DummyTestDetailSerializer,
            "partial_update": DummyTestDetailSerializer,
        }

        config = create_schema_view_config(
            model_class=MockSerializerModel,
            display_config={
                "tag": "Test Models",
            },
            request_serializers=req_serializers,
            response_serializers=res_serializers,
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

    def test_config_with_additional_responses(self):
        additional_responses = {
            "list": {422: DummyTestSerializer},
            "create": {409: DummyTestSerializer},
        }

        req_serializers: RequestSerializersConfig = {
            "create": DummyTestWriteSerializer,
            "update": DummyTestWriteSerializer,
            "partial_update": DummyTestWriteSerializer,
        }

        res_serializers: ResponseSerializersConfig = {
            "create": DummyTestDetailSerializer,
            "list": DummyTestSerializer,
            "retrieve": DummyTestDetailSerializer,
            "update": DummyTestDetailSerializer,
            "partial_update": DummyTestDetailSerializer,
        }

        config = create_schema_view_config(
            model_class=MockSerializerModel,
            display_config={
                "tag": "Test Models",
            },
            request_serializers=req_serializers,
            response_serializers=res_serializers,
            additional_responses=additional_responses,
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

    def test_config_request_response_serializers(self):
        req_serializers: RequestSerializersConfig = {
            "create": DummyTestWriteSerializer,
            "update": DummyTestWriteSerializer,
            "partial_update": DummyTestWriteSerializer,
        }

        res_serializers: ResponseSerializersConfig = {
            "create": DummyTestDetailSerializer,
            "list": DummyTestSerializer,
            "retrieve": DummyTestDetailSerializer,
            "update": DummyTestDetailSerializer,
            "partial_update": DummyTestDetailSerializer,
        }

        config = create_schema_view_config(
            model_class=MockSerializerModel,
            display_config={
                "tag": "Test Models",
            },
            request_serializers=req_serializers,
            response_serializers=res_serializers,
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

    def test_config_function_returns_decorator(self):
        req_serializers: RequestSerializersConfig = {
            "create": DummyTestWriteSerializer,
            "update": DummyTestWriteSerializer,
            "partial_update": DummyTestWriteSerializer,
        }

        res_serializers: ResponseSerializersConfig = {
            "create": DummyTestDetailSerializer,
            "list": DummyTestSerializer,
            "retrieve": DummyTestDetailSerializer,
            "update": DummyTestDetailSerializer,
            "partial_update": DummyTestDetailSerializer,
        }

        config = create_schema_view_config(
            model_class=MockSerializerModel,
            display_config={
                "tag": "Test Models",
            },
            request_serializers=req_serializers,
            response_serializers=res_serializers,
        )

        @config["list"]
        def dummy_list_view():
            pass

        @config["create"]
        def dummy_create_view():
            pass

        assert callable(dummy_list_view)
        assert callable(dummy_create_view)
