"""The endpoint BOX NOW calls: its parcel-event webhook.

The carrier's ``webhook_urlconf``, mounted at the root of both hosts by
``shipping.webhook_urls``: the path is the one registered with BOX NOW, so
it takes no ``api/v1/`` and no language prefix.
"""

from __future__ import annotations

from django.urls import path

from shipping_boxnow.views.webhook import BoxNowWebhookView

urlpatterns = [
    path(
        "boxnow/webhook/",
        BoxNowWebhookView.as_view(),
        name="boxnow-webhook",
    ),
]
