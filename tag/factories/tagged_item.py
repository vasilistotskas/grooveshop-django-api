import factory
from django.contrib.contenttypes.models import ContentType

from core.helpers.factory import get_or_create_instance
from tag.models.tagged_item import TaggedItem


def get_or_create_product():
    return get_or_create_instance(
        app_label="product",
        model_name="Product",
        factory_module_path="product.factories.product",
        factory_class_name="ProductFactory",
    )


def get_or_create_tag():
    return get_or_create_instance(
        app_label="tag",
        model_name="Tag",
        factory_module_path="tag.factories.tag",
        factory_class_name="TagFactory",
    )


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
