from django.urls import path

from agent.staff_views import (
    StaffAgentCustomersView,
    StaffAgentMessagesView,
    StaffAgentMeView,
    StaffAgentOrderDetailView,
    StaffAgentOrdersView,
    StaffAgentProductsView,
    StaffAgentProfitView,
    StaffAgentReviewsView,
    StaffAgentSalesView,
)
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
    # The store-admin surface behind /mcp/admin (agent/staff_views.py).
    path("agent/staff/me", StaffAgentMeView.as_view(), name="agent-staff-me"),
    path(
        "agent/staff/orders",
        StaffAgentOrdersView.as_view(),
        name="agent-staff-orders",
    ),
    path(
        "agent/staff/orders/<int:pk>",
        StaffAgentOrderDetailView.as_view(),
        name="agent-staff-order",
    ),
    path(
        "agent/staff/sales",
        StaffAgentSalesView.as_view(),
        name="agent-staff-sales",
    ),
    path(
        "agent/staff/profit",
        StaffAgentProfitView.as_view(),
        name="agent-staff-profit",
    ),
    path(
        "agent/staff/products",
        StaffAgentProductsView.as_view(),
        name="agent-staff-products",
    ),
    path(
        "agent/staff/customers",
        StaffAgentCustomersView.as_view(),
        name="agent-staff-customers",
    ),
    path(
        "agent/staff/reviews",
        StaffAgentReviewsView.as_view(),
        name="agent-staff-reviews",
    ),
    path(
        "agent/staff/messages",
        StaffAgentMessagesView.as_view(),
        name="agent-staff-messages",
    ),
]
