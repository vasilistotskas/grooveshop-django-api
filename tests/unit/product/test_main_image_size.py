"""A product's card and detail carry its main image's stored size, read
with one query when the queryset did not prefetch the main image."""

from __future__ import annotations

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from PIL import Image

from product.factories.image import ProductImageFactory
from product.factories.product import ProductFactory
from product.models.product import Product
from product.serializers.product import ProductCardSerializer

pytestmark = pytest.mark.django_db


def _jpeg(size: tuple[int, int]) -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", size, "red").save(buffer, "JPEG")
    return SimpleUploadedFile("main.jpg", buffer.getvalue())


def test_the_main_image_size_is_read_once_without_a_prefetch():
    product = ProductFactory(num_images=0, num_reviews=0)
    ProductImageFactory(product=product, is_main=True, image=_jpeg((800, 600)))
    ProductImageFactory(product=product, is_main=False, image=_jpeg((90, 90)))
    fresh = Product.objects.get(pk=product.pk)

    with CaptureQueriesContext(connection) as queries:
        data = ProductCardSerializer(fresh).data

    assert (data["main_image_width"], data["main_image_height"]) == (800, 600)
    assert data["main_image_path"].endswith(".jpg")
    main_image_reads = [
        q["sql"]
        for q in queries.captured_queries
        if "product_productimage" in q["sql"] and '"is_main"' in q["sql"]
    ]
    assert len(main_image_reads) == 1


def test_a_product_without_a_main_image_has_no_size():
    product = ProductFactory(num_images=0, num_reviews=0)

    data = ProductCardSerializer(Product.objects.get(pk=product.pk)).data

    assert data["main_image_path"] == ""
    assert (data["main_image_width"], data["main_image_height"]) == (
        None,
        None,
    )
