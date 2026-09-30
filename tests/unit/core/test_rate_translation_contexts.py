"""``"Rate"`` means three different things, so each use carries a context.

A product review's star rating, a shipping price and a VAT percentage
all shared one ``msgid "Rate"``, so the invoice's VAT breakdown was
headed "Βαθμολογία" (a review score) on every Greek invoice.
"""

from django.template.loader import render_to_string
from django.utils import translation
from django.utils.translation import gettext, pgettext

from product.models.review import ProductReview
from shipping.admin import ShippingRateInline


def test_each_rate_meaning_has_its_own_greek_translation():
    with translation.override("el"):
        assert gettext("Rate") == "Βαθμολογία"
        assert pgettext("VAT rate", "Rate") == "Συντελεστής"
        assert pgettext("shipping", "Rate") == "Τιμή αποστολής"


def test_review_rate_field_keeps_the_rating_translation():
    field = ProductReview._meta.get_field("rate")
    with translation.override("el"):
        assert str(field.verbose_name) == "Βαθμολογία"


def test_shipping_rate_inline_is_labelled_as_a_price():
    with translation.override("el"):
        assert str(ShippingRateInline.verbose_name) == "Τιμή αποστολής"


def test_invoice_vat_breakdown_is_headed_by_the_vat_rate():
    with translation.override("el"):
        html = render_to_string("invoices/invoice.html", {})
    assert "Συντελεστής" in html
    assert "Βαθμολογία" not in html
