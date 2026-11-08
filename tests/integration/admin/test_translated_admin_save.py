"""An admin edit of a translated field persists, through the whole stack.

2026-04-27: a product's description edited in the admin came back
unchanged after "Save". The cause (traced 2026-11-08): the change form
edits an instance ``ProductAdmin.get_queryset`` loaded with ``.only()``,
``Product.save`` reads a deferred field for its pricing plan, Django loads
it with ``refresh_from_db(fields=[...])``, and parler's override cleared
every cached translation, the form's edit included.
``core.apps._keep_translations_on_a_partial_refresh`` keeps them. These
tests drive the change form the way a browser does - log in, open the
page, post every field it renders with one translated value changed - and
read the translation row back from the database.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from product.factories.product import ProductFactory
from product.models.product import Product, ProductTranslation
from tag.factories.tag import TagFactory
from tag.models.tag import Tag
from tenant.models import TenantMembershipRole, UserTenantMembership
from tests.utils.staff import store_tenant
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

HOST = "translated-save.example.com"

#: Django loading one deferred column of a product.
COLUMN_RELOAD = re.compile(
    r'^SELECT "product_product"\."id", "product_product"\."\w+" FROM '
    r'"product_product" WHERE "product_product"\."id" = '
)


class _FormFields(HTMLParser):
    """The fields a browser would post from the change form."""

    def __init__(self):
        super().__init__()
        self.fields: dict[str, str] = {}
        self._in_form = False
        self._textarea: str | None = None
        self._select: str | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form" and (a.get("id") or "").endswith("_form"):
            self._in_form = True
        if not self._in_form:
            return
        name = a.get("name")
        if tag == "input" and name:
            kind = a.get("type", "text")
            if kind in ("checkbox", "radio"):
                if "checked" in a:
                    self.fields[name] = a.get("value") or "on"
            elif kind not in ("submit", "button", "file", "image"):
                self.fields[name] = a.get("value") or ""
        elif tag == "textarea" and name:
            self._textarea = name
            self.fields[name] = ""
        elif tag == "select" and name:
            self._select = name
        elif tag == "option" and self._select and "selected" in a:
            self.fields[self._select] = a.get("value") or ""

    def handle_endtag(self, tag):
        if tag == "form":
            self._in_form = False
        elif tag == "textarea":
            self._textarea = None
        elif tag == "select":
            self._select = None

    def handle_data(self, data):
        if self._textarea:
            self.fields[self._textarea] += data


@pytest.fixture
def staff_client(bind_tenant_and_schema):
    tenant = store_tenant("translated_save")
    owner = UserAccountFactory(is_staff=True, plain_password="pw12345")
    UserTenantMembership.objects.create(
        user=owner,
        tenant=tenant,
        role=TenantMembershipRole.OWNER,
        is_active=True,
    )
    bind_tenant_and_schema(tenant)
    client = Client()
    response = client.post(
        "/admin/login/",
        {
            "step": "password",
            "username": owner.email,
            "password": "pw12345",
            "next": "/admin/",
        },
        SERVER_NAME=HOST,
    )
    assert response.status_code == 302, response.content[:500]
    return client


def _edit(client, url: str, field: str, value: str) -> None:
    page = client.get(url, SERVER_NAME=HOST)
    assert page.status_code == 200
    form = _FormFields()
    form.feed(page.content.decode())
    assert field in form.fields, sorted(form.fields)

    response = client.post(
        url, {**form.fields, field: value, "_save": "Save"}, SERVER_NAME=HOST
    )

    assert response.status_code == 302, response.content[:1500]


def test_a_product_description_edit_persists(staff_client):
    product = ProductFactory(active=True, num_images=0, num_reviews=0)
    url = reverse("admin:product_product_change", args=[product.pk])

    _edit(staff_client, url, "description", "<p>after</p>")

    row = ProductTranslation.objects.get(master=product, language_code="el")
    assert row.description == "<p>after</p>"


def test_a_tag_label_edit_persists(staff_client):
    tag = TagFactory(label="before", active=True)
    url = reverse("admin:tag_tag_change", args=[tag.pk])

    _edit(staff_client, url, "label", "after")

    row = Tag.objects.get(pk=tag.pk).translations.get(language_code="el")
    assert row.label == "after"


def test_a_deferred_read_keeps_an_unsaved_translation():
    """The mechanism itself: set a translated value on an instance with a
    deferred column, read that column, then save. The read is also the
    control for ``COLUMN_RELOAD``: the pattern matches the query Django
    really sends, so the whole-row test below cannot pass by matching
    nothing."""
    product = ProductFactory(active=True, num_images=0, num_reviews=0)
    loaded = Product.objects.defer("stock").get(pk=product.pk)
    loaded.set_current_language("el")
    loaded.description = "<p>kept</p>"

    with CaptureQueriesContext(connection) as captured:
        assert loaded.stock == product.stock
    assert any(COLUMN_RELOAD.match(q["sql"]) for q in captured)
    loaded.save()

    row = ProductTranslation.objects.get(master=product, language_code="el")
    assert row.description == "<p>kept</p>"


def test_the_change_form_saves_a_whole_row(staff_client):
    """The changelist's ``.only()`` stays on the changelist
    (``BaseModelAdmin.serves_changelist``): a save reloaded 15 deferred
    columns one query at a time, each a point where parler dropped the
    form's translations."""
    product = ProductFactory(active=True, num_images=0, num_reviews=0)
    url = reverse("admin:product_product_change", args=[product.pk])

    with CaptureQueriesContext(connection) as captured:
        _edit(staff_client, url, "description", "<p>after</p>")

    reloads = [q["sql"] for q in captured if COLUMN_RELOAD.match(q["sql"])]
    assert reloads == []
