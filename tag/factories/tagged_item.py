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
    tag = factory.LazyFunction(get_or_create_tag)

    class Meta:
        abstract = True

    @classmethod
    def _create(cls, model_class, **kwargs):
        content_object = kwargs.pop("content_object", None)

        if content_object is None:
            raise ValueError("content_object is required for TaggedItem")

        if hasattr(content_object, "pk") and content_object.pk is None:
            content_object.save()

        content_type = ContentType.objects.get_for_model(content_object)
        content_type = ContentType.objects.get(pk=content_type.pk)

        kwargs.update(
            {
                "content_type": content_type,
                "object_id": content_object.pk,
            }
        )

        return super()._create(model_class, **kwargs)


class TaggedProductFactory(TaggedItemFactory):
    content_object = factory.LazyFunction(get_or_create_product)

    class Meta:
        model = TaggedItem
