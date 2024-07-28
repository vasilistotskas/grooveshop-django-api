import factory
from django.contrib.contenttypes.models import ContentType

from tag.models.tagged_item import TaggedItem


class TaggedItemFactory(factory.django.DjangoModelFactory):
    object_id = factory.SelfAttribute("content_object.id")
    content_type = factory.LazyAttribute(lambda o: ContentType.objects.get_for_model(o.content_object))

    class Meta:
        exclude = ["content_object"]
        abstract = True


class TaggedProductFactory(TaggedItemFactory):
    content_object = factory.SubFactory("product.factories.product.ProductFactory")
    tag = factory.SubFactory("tag.factories.tag.TagFactory")

    class Meta:
        model = TaggedItem
