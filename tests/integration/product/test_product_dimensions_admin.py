"""Length, width and height in the product admin, next to weight."""

from unittest.mock import Mock

import pytest
from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory
from measurement.measures import Distance

from core.forms.measurement import MeasurementWidget
from core.units import DistanceUnits
from product.admin import ProductAdmin
from product.factories import ProductFactory
from product.models import Product

DIMENSIONS = ("length", "width", "height")


@pytest.fixture
def product_admin():
    return ProductAdmin(Product, AdminSite())


@pytest.fixture
def admin_request():
    request = RequestFactory().get("/admin/")
    request.user = Mock()
    request.user.is_authenticated = True
    request.user.is_staff = True
    request.user.is_superuser = True
    return request


def _flat_fields(admin):
    return [
        name
        for _title, options in admin.fieldsets
        for name in options["fields"]
    ]


def test_dimensions_sit_after_weight_in_the_pricing_and_inventory_tab(
    product_admin,
):
    fields = _flat_fields(product_admin)
    assert fields[fields.index("weight") + 1 : fields.index("weight") + 4] == [
        *DIMENSIONS
    ]


@pytest.mark.django_db
def test_form_renders_a_distance_widget_for_each_dimension(
    product_admin, admin_request
):
    form = product_admin.get_form(admin_request)()

    for name in DIMENSIONS:
        field = form.fields[name]
        assert isinstance(field.widget, MeasurementWidget)
        assert field.widget.unit_choices == DistanceUnits.CHOICES
        assert field.required is False
        assert name in form.as_p()


@pytest.mark.django_db
def test_dimension_left_empty_is_none_and_one_filled_is_a_distance(
    product_admin, admin_request
):
    fields = product_admin.get_form(admin_request).base_fields

    assert fields["length"].clean(["", "cm"]) is None
    assert fields["length"].clean(["30", "cm"]).cm == 30
    assert fields["width"].clean(["250", "mm"]).cm == 25


@pytest.mark.django_db
def test_a_product_without_dimensions_is_valid_and_they_persist():
    product = ProductFactory(num_images=0, num_reviews=0)
    assert (product.length, product.width, product.height) == (None,) * 3

    product.length = Distance(cm=30)
    product.save()
    product.refresh_from_db()

    assert product.length.cm == pytest.approx(30)
    assert product.width is None


@pytest.mark.django_db
def test_csv_export_carries_the_three_dimensions(product_admin, admin_request):
    import csv
    import io

    from django.contrib.messages.storage.fallback import FallbackStorage

    from admin.export import ExportActionMixin

    admin_request.session = {}
    admin_request._messages = FallbackStorage(admin_request)
    measured = ProductFactory(num_images=0, num_reviews=0)
    measured.length = Distance(cm=30)
    measured.width = Distance(cm=20)
    measured.height = Distance(cm=5)
    measured.save()
    bare = ProductFactory(num_images=0, num_reviews=0)

    response = ExportActionMixin.export_csv(
        product_admin,
        admin_request,
        Product.objects.filter(pk__in=[measured.pk, bare.pk]).order_by("pk"),
    )

    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8"))))
    header, first, second = rows
    index = {
        name: header.index(str(Product._meta.get_field(name).verbose_name))
        for name in DIMENSIONS
    }
    assert [first[index[name]] for name in index] == [
        "30.0 cm",
        "20.0 cm",
        "5.0 cm",
    ]
    assert [second[index[name]] for name in index] == ["", "", ""]
