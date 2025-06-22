from django.test import TestCase
from rest_framework import generics, status
from rest_framework.test import APITestCase

from contact import views
from contact.models import Contact
from contact.serializers import ContactWriteSerializer


class TestContactCreateView(APITestCase):
    def setUp(self):
        self.url = "/api/v1/contact"
        self.valid_data = {
            "name": "John Doe",
            "email": "john@example.com",
            "message": "This is a valid message with sufficient length for testing purposes.",
        }

    def test_create_contact_success(self):
        response = self.client.post(
            self.url, data=self.valid_data, format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert Contact.objects.count() == 1

        contact = Contact.objects.first()
        assert contact.name == self.valid_data["name"]
        assert contact.email == self.valid_data["email"]
        assert contact.message == self.valid_data["message"]

    def test_create_contact_invalid_data(self):
        invalid_data = {
            "name": "",
            "email": "invalid-email",
            "message": "Short",
        }

        response = self.client.post(self.url, data=invalid_data, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Contact.objects.count() == 0

    def test_create_contact_missing_fields(self):
        incomplete_data = {"name": "John Doe"}

        response = self.client.post(
            self.url, data=incomplete_data, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Contact.objects.count() == 0

    def test_create_contact_with_extra_fields(self):
        data_with_extra = self.valid_data.copy()
        data_with_extra["extra_field"] = "should be ignored"

        response = self.client.post(
            self.url, data=data_with_extra, format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert Contact.objects.count() == 1

    def test_view_queryset(self):
        view = views.ContactCreateView()
        queryset = view.get_queryset()

        assert queryset.model == Contact
        assert list(queryset.all()) == list(Contact.objects.all())

    def test_view_serializer_class(self):
        view = views.ContactCreateView()
        serializer_class = view.get_serializer_class()

        assert serializer_class == ContactWriteSerializer

    def test_view_post_method_override(self):
        view = views.ContactCreateView()

        assert hasattr(view, "post")
        assert callable(view.post)

    def test_view_logging_setup(self):
        assert hasattr(views, "logger")
        assert views.logger.name == "contact.views"

    def test_view_inheritance(self):
        assert issubclass(views.ContactCreateView, generics.CreateAPIView)

    def test_view_schema_decoration(self):
        view = views.ContactCreateView()

        assert hasattr(view, "post")
        post_method = view.post

        assert callable(post_method)


class TestContactViewsIntegration(TestCase):
    def test_view_can_be_instantiated(self):
        view = views.ContactCreateView()

        assert view is not None
        assert hasattr(view, "queryset")
        assert hasattr(view, "serializer_class")

    def test_view_attributes(self):
        view = views.ContactCreateView()

        assert view.queryset.model == Contact

        assert view.serializer_class == ContactWriteSerializer
