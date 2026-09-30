"""``core.context_processors.metadata`` costs nothing until a template
reads it.

A context processor runs for every template rendered with a
``RequestContext`` - Unfold renders each ``{% component %}`` that way -
so the admin dashboard used to resolve the tenant's contact email 59
times per page (118 ``extra_settings`` queries) for values no admin
template reads.
"""

from __future__ import annotations

from unittest import mock

from django.contrib.auth.models import AnonymousUser
from django.template import engines
from django.test import RequestFactory

from core.context_processors import metadata


def _request():
    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    return request


def test_nothing_is_resolved_until_read():
    with (
        mock.patch("core.context_processors.tenant_contact_email") as email,
        mock.patch("core.context_processors.tenant_site_name") as name,
        mock.patch("core.context_processors.get_tenant_base_url") as url,
    ):
        metadata(_request())

    email.assert_not_called()
    name.assert_not_called()
    url.assert_not_called()


def test_a_template_that_reads_a_value_gets_it():
    template = engines["django"].from_string(
        "{{ INFO_EMAIL }}|{% if INFO_EMAIL %}set{% endif %}"
    )
    with mock.patch(
        "core.context_processors.tenant_contact_email",
        return_value="shop@example.com",
    ):
        context = metadata(_request())
        rendered = template.render(context)

    assert rendered == "shop@example.com|set"


def test_request_details_only_for_superusers():
    request = _request()
    assert dict(metadata(request)["REQUEST_DETAILS"]) == {}
