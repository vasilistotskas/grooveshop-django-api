"""A pickup-point order after it is placed: staff can still save it, and
its owner cannot write a buyer's address over the point's.

``_deliver_to_pickup_point`` stores the point's address, with an empty
``street_number`` where the point's street line names no number.
"""

from __future__ import annotations

import pytest
from django import forms
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.urls import reverse
from rest_framework.test import APIClient

from order.admin import OrderAdmin
from order.factories.order import OrderFactory
from order.models.order import Order
from pay_way.factories import PayWayFactory
from shipping.enum import ShippingKind
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

#: A valid Greek landline: the factory's random number is not always one,
#: and the form would then refuse the phone instead of the address.
PHONE = "+302101234567"


def _pickup_order(**fields) -> Order:
    return OrderFactory.create(
        shipping_kind=ShippingKind.PICKUP_POINT,
        street="Πειραιώς 193, Πετράλωνα",
        street_number="",
        address_line_2="",
        floor="",
        location_type="",
        phone=PHONE,
        pay_way=PayWayFactory(),
        num_order_items=0,
        **fields,
    )


def _change_form(order: Order, **changes) -> forms.ModelForm:
    """The admin's change form for ``order``, posted with what it shows
    and ``changes``: what a staff member's save sends."""
    request = RequestFactory().post("/")
    request.user = get_user_model()(id=1, username="staff", is_superuser=True)
    form_class = OrderAdmin(Order, AdminSite()).get_form(request, order)
    shown = form_class(instance=order)
    data: dict[str, object] = {}
    for field in shown:
        value = field.value()
        widget = field.field.widget
        if isinstance(widget, forms.MultiWidget):
            parts = (
                value
                if isinstance(value, (list, tuple))
                else widget.decompress(value)
            )
            for suffix, part in zip(widget.widgets_names, parts, strict=True):
                if part is not None:
                    data[f"{field.html_name}{suffix}"] = part
        elif value is not None:
            # A phone is posted in international form, as the widget shows
            # one of another country.
            data[field.html_name] = getattr(value, "as_e164", value)
    data.update(changes)
    return form_class(data=data, instance=order)


class TestTheAdmin:
    def test_saves_a_pickup_order_whose_point_names_no_number(self):
        order = _pickup_order()

        form = _change_form(order, customer_notes="Call before delivery")

        assert form.is_valid(), form.errors
        form.save()
        order.refresh_from_db()
        assert order.customer_notes == "Call before delivery"
        assert order.street_number == ""

    def test_still_needs_a_number_on_a_home_delivery(self):
        order = OrderFactory(
            shipping_kind=ShippingKind.HOME_DELIVERY,
            phone=PHONE,
            pay_way=PayWayFactory(),
            num_order_items=0,
        )

        form = _change_form(order, street_number="")

        assert not form.is_valid()
        assert list(form.errors) == ["street_number"]


class TestTheOwner:
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("addressLine2", "3ος όροφος"),
            ("street", "Ερμού"),
            ("streetNumber", "12"),
            ("floor", "SECOND_FLOOR"),
        ],
    )
    def test_cannot_write_an_address_over_the_points(self, field, value):
        owner = UserAccountFactory()
        order = _pickup_order(user=owner)
        client = APIClient()
        client.force_authenticate(owner)

        response = client.patch(
            reverse("order-detail", args=[order.pk]),
            {field: value},
            format="json",
        )

        assert response.status_code == 400, response.content
        assert response.json()["fields"][field][0]["code"] == (
            "order_field_locked"
        )
        order.refresh_from_db()
        assert order.address_line_2 == ""
        assert order.street == "Πειραιώς 193, Πετράλωνα"

    def test_can_still_change_what_is_not_the_address(self):
        owner = UserAccountFactory()
        order = _pickup_order(user=owner)
        client = APIClient()
        client.force_authenticate(owner)

        response = client.patch(
            reverse("order-detail", args=[order.pk]),
            {"customerNotes": "Call before delivery"},
            format="json",
        )

        assert response.status_code == 200, response.content
        order.refresh_from_db()
        assert order.customer_notes == "Call before delivery"
