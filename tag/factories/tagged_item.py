import importlib

import factory
from django.apps import apps
from django.contrib.contenttypes.models import ContentType

from tag.models.tagged_item import TaggedItem


def get_or_create_product():
    if apps.get_model("product", "Product").objects.exists():
        return apps.get_model("product", "Product").objects.order_by("?").first()
    else:
        product_factory_module = importlib.import_module("product.factories.product")
        product_factory_class = getattr(product_factory_module, "ProductFactory")
        return product_factory_class.create()


def get_or_create_tag():
    if apps.get_model("tag", "Tag").objects.exists():
        return apps.get_model("tag", "Tag").objects.order_by("?").first()
    else:
        tag_factory_module = importlib.import_module("tag.factories.tag")
        tag_factory_class = getattr(tag_factory_module, "TagFactory")
        return tag_factory_class.create()


class TaggedItemFactory(factory.django.DjangoModelFactory):
    object_id = factory.SelfAttribute("content_object.id")
    content_type = factory.LazyAttribute(lambda o: ContentType.objects.get_for_model(o.content_object))

    class Meta:
        exclude = ["content_object"]
        abstract = True


class TaggedProductFactory(TaggedItemFactory):
    content_object = factory.LazyFunction(get_or_create_product)
    tag = factory.LazyFunction(get_or_create_tag)

    class Meta:
        model = TaggedItem
