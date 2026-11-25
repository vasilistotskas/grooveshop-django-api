"""The endpoints the couriers call: each registered carrier's
``webhook_urlconf``.

Mounted at the root of both hosts in ``src/core/urls.py``, outside
``i18n_patterns``: a courier posts to the path registered with it, with no
language prefix.
"""

from __future__ import annotations

from django.urls import include, path

from shipping.interfaces import registered_providers

urlpatterns = [
    path("", include(carrier.webhook_urlconf))
    for carrier in registered_providers()
    if carrier.webhook_urlconf is not None
]
