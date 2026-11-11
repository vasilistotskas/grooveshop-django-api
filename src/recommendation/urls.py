from django.urls import path
from rest_framework.urlpatterns import format_suffix_patterns

from recommendation.views import recommendation_event, recommendations

urlpatterns = [
    path("recommendations", recommendations, name="recommendation-list"),
    path(
        "recommendations/events",
        recommendation_event,
        name="recommendation-event",
    ),
]

urlpatterns = format_suffix_patterns(urlpatterns)
