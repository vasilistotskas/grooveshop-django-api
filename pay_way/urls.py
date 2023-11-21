from django.urls import path

from pay_way.views import PayWayViewSet

urlpatterns = [
    path(
        "pay_way",
        PayWayViewSet.as_view({"get": "list", "post": "create"}),
        name="payway-list",
    ),
    path(
        "pay_way/<str:pk>",
        PayWayViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
        name="payway-detail",
    ),
]
