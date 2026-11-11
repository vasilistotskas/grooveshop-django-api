import importlib

import factory
from django.apps import apps

from devtools.factories import CustomDjangoModelFactory
from product.models.product_attribute import ProductAttribute


def get_or_create_product():
    """Get or create a product for the product attribute."""
    if apps.get_model("product", "Product").objects.exists():
        return (
            apps.get_model("product", "Product").objects.order_by("?").first()
        )
    else:
        product_factory_module = importlib.import_module(
            "product.factories.product"
        )
        product_factory_class = product_factory_module.ProductFactory
        return product_factory_class.create()


def get_or_create_attribute_value():
    """Get or create an attribute value for the product attribute."""
    if apps.get_model("product", "AttributeValue").objects.exists():
        return (
            apps.get_model("product", "AttributeValue")
            .objects.order_by("?")
            .first()
        )
    else:
        attribute_value_factory_module = importlib.import_module(
            "product.factories.attribute_value"
        )
        attribute_value_factory_class = (
            attribute_value_factory_module.AttributeValueFactory
        )
        return attribute_value_factory_class.create()


class ProductAttributeFactory(CustomDjangoModelFactory):
    product = factory.LazyFunction(get_or_create_product)
    attribute_value = factory.LazyFunction(get_or_create_attribute_value)

    class Meta:
        model = ProductAttribute
