import factory

from builder.models import (
    BuilderDocument,
    BuilderReference,
    BuilderRelease,
    BuilderVersion,
    DocumentKind,
    ReferenceKind,
)


class BuilderDocumentFactory(factory.django.DjangoModelFactory):
    kind = DocumentKind.PAGE
    key = factory.Sequence(lambda n: f"page:page-{n}")

    class Meta:
        model = BuilderDocument


class BuilderVersionFactory(factory.django.DjangoModelFactory):
    document = factory.SubFactory(BuilderDocumentFactory)
    number = 1
    data = factory.LazyFunction(lambda: {"sections": {}, "order": []})
    catalogue_hash = "hash"
    content_hash = factory.Sequence(lambda n: f"{n:064x}")
    size_bytes = 32

    class Meta:
        model = BuilderVersion


class BuilderReleaseFactory(factory.django.DjangoModelFactory):
    number = factory.Sequence(lambda n: n + 1)
    versions = factory.LazyFunction(dict)

    class Meta:
        model = BuilderRelease


class BuilderReferenceFactory(factory.django.DjangoModelFactory):
    kind = ReferenceKind.PRODUCT
    target = factory.Sequence(str)
    document = "template:home"
    node_path = "/sections/hero/settings/productId"

    class Meta:
        model = BuilderReference
