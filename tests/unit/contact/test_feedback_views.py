from django.test import TestCase
from rest_framework import generics, status
from rest_framework.test import APITestCase

from contact import views
from contact.models import Feedback, FeedbackCategory
from contact.serializers import FeedbackWriteSerializer


class TestFeedbackCreateView(APITestCase):
    def setUp(self):
        self.url = "/api/v1/feedback"
        self.valid_data = {
            "name": "John Doe",
            "email": "john@example.com",
            "rating": 5,
            "category": FeedbackCategory.WEBSITE,
            "message": "This checkout experience was smooth and fast.",
        }

    def test_create_feedback_success(self):
        response = self.client.post(
            self.url, data=self.valid_data, format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert Feedback.objects.count() == 1

        feedback = Feedback.objects.first()
        assert feedback.name == self.valid_data["name"]
        assert feedback.rating == self.valid_data["rating"]
        assert feedback.category == self.valid_data["category"]

    def test_create_anonymous_feedback_success(self):
        data = {
            "rating": 4,
            "category": FeedbackCategory.SUPPORT,
            "message": "Support was quick to respond to my request.",
        }

        response = self.client.post(self.url, data=data, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert Feedback.objects.count() == 1

        feedback = Feedback.objects.first()
        assert feedback.name == ""
        assert feedback.email == ""

    def test_create_feedback_invalid_rating(self):
        invalid_data = self.valid_data.copy()
        invalid_data["rating"] = 42

        response = self.client.post(self.url, data=invalid_data, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Feedback.objects.count() == 0

    def test_create_feedback_missing_required_fields(self):
        response = self.client.post(self.url, data={}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Feedback.objects.count() == 0

    def test_create_feedback_spam_message_rejected(self):
        spam_data = self.valid_data.copy()
        spam_data["message"] = (
            "Best deal ever! Guaranteed lowest price! Act now!"
        )

        response = self.client.post(self.url, data=spam_data, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Feedback.objects.count() == 0

    def test_view_queryset(self):
        view = views.FeedbackCreateView()
        queryset = view.get_queryset()

        assert queryset.model == Feedback
        assert list(queryset.all()) == list(Feedback.objects.all())

    def test_view_serializer_class(self):
        view = views.FeedbackCreateView()
        serializer_class = view.get_serializer_class()

        assert serializer_class == FeedbackWriteSerializer

    def test_view_inheritance(self):
        assert issubclass(views.FeedbackCreateView, generics.CreateAPIView)


class TestFeedbackViewsIntegration(TestCase):
    def test_view_can_be_instantiated(self):
        view = views.FeedbackCreateView()

        assert view is not None
        assert hasattr(view, "queryset")
        assert hasattr(view, "serializer_class")

    def test_view_attributes(self):
        view = views.FeedbackCreateView()

        assert view.queryset.model == Feedback
        assert view.serializer_class == FeedbackWriteSerializer
