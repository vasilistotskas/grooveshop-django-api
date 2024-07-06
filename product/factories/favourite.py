import factory

from product.models.product import ProductFavourite


class ProductFavouriteFactory(factory.django.DjangoModelFactory):
    user = factory.SubFactory("user.factories.account.UserAccountFactory")
    product = factory.SubFactory("product.factories.product.ProductFactory")

    class Meta:
        model = ProductFavourite
        django_get_or_create = ("user", "product")
