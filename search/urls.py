from search import views
from django.urls import path
from rest_framework.urlpatterns import format_suffix_patterns

urlpatterns = [
    path("search-product", views.SearchProduct.as_view()),
]

urlpatterns = format_suffix_patterns(urlpatterns)
