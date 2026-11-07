from django.urls import path

from agent.views import (
    AgentFavouritesView,
    AgentInternalOrderView,
    AgentLoyaltyView,
    AgentMeView,
    AgentOrdersView,
)

urlpatterns = [
    path(
        "agent/internal/order/<uuid:uuid>",
        AgentInternalOrderView.as_view(),
        name="agent-internal-order",
    ),
    path("agent/me", AgentMeView.as_view(), name="agent-me"),
    path("agent/me/orders", AgentOrdersView.as_view(), name="agent-orders"),
    path("agent/me/loyalty", AgentLoyaltyView.as_view(), name="agent-loyalty"),
    path(
        "agent/me/favourites",
        AgentFavouritesView.as_view(),
        name="agent-favourites",
    ),
]
