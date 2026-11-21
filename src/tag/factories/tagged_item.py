import importlib

import factory
from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ImproperlyConfigured

from tag.models.tagged_item import TaggedItem

_factory_cache = {}


def get_or_create_instance(
    app_label, model_name, factory_module_path, factory_class_name
):
    try:
        Model = apps.get_model(app_label, model_name)
    except LookupError as err:
        raise ImproperlyConfigured(
            f"Model '{model_name}' not found in app '{app_label}'."
        ) from err

    if Model.objects.exists():
        return Model.objects.order_by("?").first()
    else:
        cache_key = f"{factory_module_path}.{factory_class_name}"
        if cache_key in _factory_cache:
            factory_class = _factory_cache[cache_key]
        else:
            try:
                factory_module = importlib.import_module(factory_module_path)
                factory_class = getattr(factory_module, factory_class_name)
                _factory_cache[cache_key] = factory_class
            except ModuleNotFoundError as err:
                raise ImproperlyConfigured(
                    f"Factory module '{factory_module_path}' not found."
                ) from err
            except AttributeError as err:
                raise ImproperlyConfigured(
                    f"Factory class '{factory_class_name}' not found in '{factory_module_path}'."
                ) from err

        return factory_class.create()


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
