import json
from unittest.mock import Mock

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.test import TestCase
from rest_framework import serializers
from rest_framework.test import APIRequestFactory
from rest_framework.viewsets import ModelViewSet

from core.utils.serializers import (
    MultiSerializerMixin,
    TranslatedFieldExtended,
    create_schema_view_config,
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


class DummyTestViewSet(MultiSerializerMixin, ModelViewSet):
    serializers = {
        "list": DummyTestSerializer,
        "retrieve": DummyTestDetailSerializer,
        "create": DummyTestWriteSerializer,
        "update": DummyTestWriteSerializer,
        "partial_update": DummyTestWriteSerializer,
    }
    response_serializers = {
        "create": DummyTestDetailSerializer,
        "update": DummyTestDetailSerializer,
        "partial_update": DummyTestDetailSerializer,
    }
    request_serializers = {
        "create": DummyTestWriteSerializer,
        "update": DummyTestWriteSerializer,
    }


class DummyTestViewSetWithDefault(MultiSerializerMixin, ModelViewSet):
    serializers = {
        "default": DummyTestSerializer,
        "list": DummyTestSerializer,
    }


class DummyTestViewSetWithSerializerClass(ModelViewSet):
    serializer_class = DummyTestSerializer


class DummyTestViewSetMissingSerializers(MultiSerializerMixin, ModelViewSet):
    pass


class DummyTestViewSetConflictingConfig(MultiSerializerMixin, ModelViewSet):
    serializer_class = DummyTestSerializer
    serializers = {
        "list": DummyTestSerializer,
    }


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


class TestMultiSerializerMixin(TestCase):
    def test_get_serializer_class_for_action(self):
        viewset = DummyTestViewSet()

        viewset.action = "list"
        assert viewset.get_serializer_class() == DummyTestSerializer

        viewset.action = "retrieve"
        assert viewset.get_serializer_class() == DummyTestDetailSerializer

        viewset.action = "create"
        assert viewset.get_serializer_class() == DummyTestWriteSerializer

    def test_get_serializer_class_with_default(self):
        viewset = DummyTestViewSetWithDefault()

        viewset.action = "list"
        assert viewset.get_serializer_class() == DummyTestSerializer

        viewset.action = "retrieve"
        assert viewset.get_serializer_class() == DummyTestSerializer

    def test_get_request_serializer_class(self):
        viewset = DummyTestViewSet()

        viewset.action = "create"
        assert (
            viewset.get_request_serializer_class() == DummyTestWriteSerializer
        )

        viewset.action = "list"
        assert viewset.get_request_serializer_class() == DummyTestSerializer

    def test_get_response_serializer_class(self):
        viewset = DummyTestViewSet()

        viewset.action = "create"
        assert (
            viewset.get_response_serializer_class() == DummyTestDetailSerializer
        )

        viewset.action = "list"
        assert viewset.get_response_serializer_class() == DummyTestSerializer

    def test_get_serializer_for_schema(self):
        viewset = DummyTestViewSet()
        viewset.action = "list"

        schema_info = viewset.get_serializer_for_schema("create")
        assert schema_info["request"] == DummyTestWriteSerializer
        assert schema_info["response"] == DummyTestDetailSerializer

        schema_info = viewset.get_serializer_for_schema("list")
        assert schema_info["request"] == DummyTestSerializer
        assert schema_info["response"] == DummyTestSerializer

        assert viewset.action == "list"

    def test_improper_configuration_conflicting_serializers(self):
        viewset = DummyTestViewSetConflictingConfig()
        viewset.action = "list"

        with pytest.raises(ImproperlyConfigured) as exc_info:
            viewset.get_serializer_class()

        assert (
            "should only define either `serializer_class` or `serializers`"
            in str(exc_info.value)
        )

    def test_improper_configuration_missing_serializers(self):
        viewset = DummyTestViewSetMissingSerializers()
        viewset.action = "list"

        viewset.serializers = None

        with pytest.raises(ImproperlyConfigured) as exc_info:
            viewset.get_serializer_class()

        assert "is missing the serializers attribute" in str(exc_info.value)

    def test_improper_configuration_no_serializer_found(self):
        viewset = DummyTestViewSet()
        viewset.action = "destroy"

        viewset.serializers = {
            "list": DummyTestSerializer,
            "retrieve": DummyTestDetailSerializer,
        }

        with pytest.raises(ImproperlyConfigured) as exc_info:
            viewset.get_serializer_class()

        assert "No serializer found for action 'destroy'" in str(exc_info.value)

    def test_get_serializer_context(self):
        mock_request = Mock()
        mock_request.user = Mock()
        mock_request.query_params = {}

        viewset = DummyTestViewSet()
        viewset.action = "list"
        viewset.request = mock_request
        viewset.format_kwarg = None

        context = viewset.get_serializer_context()

        assert context["action"] == "list"
        assert context["view"] == viewset
        assert context["request"] == mock_request


class TestCreateSchemaViewConfig(TestCase):
    def test_basic_config_generation(self):
        config = create_schema_view_config(
            model_class=MockSerializerModel,
            display_config={
                "tag": "Test Models",
            },
            serializers={
                "list_serializer": DummyTestSerializer,
                "detail_serializer": DummyTestDetailSerializer,
                "write_serializer": DummyTestWriteSerializer,
            },
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
        config = create_schema_view_config(
            model_class=MockSerializerModel,
            display_config={
                "tag": "Test Models",
            },
            serializers={
                "list_serializer": DummyTestSerializer,
                "detail_serializer": DummyTestDetailSerializer,
                "write_serializer": DummyTestWriteSerializer,
            },
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

        config = create_schema_view_config(
            model_class=MockSerializerModel,
            display_config={
                "tag": "Test Models",
            },
            serializers={
                "list_serializer": DummyTestSerializer,
                "detail_serializer": DummyTestDetailSerializer,
                "write_serializer": DummyTestWriteSerializer,
            },
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
        config = create_schema_view_config(
            model_class=MockSerializerModel,
            display_config={
                "tag": "Test Models",
            },
            serializers={
                "list_serializer": DummyTestSerializer,
                "detail_serializer": DummyTestDetailSerializer,
                "write_serializer": DummyTestWriteSerializer,
            },
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
        config = create_schema_view_config(
            model_class=MockSerializerModel,
            display_config={
                "tag": "Test Models",
            },
            serializers={
                "list_serializer": DummyTestSerializer,
                "detail_serializer": DummyTestDetailSerializer,
                "write_serializer": DummyTestWriteSerializer,
            },
        )

        @config["list"]
        def dummy_list_view():
            pass

        @config["create"]
        def dummy_create_view():
            pass

        assert callable(dummy_list_view)
        assert callable(dummy_create_view)
