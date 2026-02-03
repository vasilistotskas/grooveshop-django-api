from product.views.attribute import AttributeViewSet
from product.views.attribute_value import AttributeValueViewSet
from product.views.category import ProductCategoryViewSet
from product.views.category_image import ProductCategoryImageViewSet
from product.views.favourite import ProductFavouriteViewSet
from product.views.image import ProductImageViewSet
from product.views.product import ProductViewSet
from product.views.review import ProductReviewViewSet

__all__ = [
    "AttributeViewSet",
    "AttributeValueViewSet",
    "ProductCategoryViewSet",
    "ProductCategoryImageViewSet",
    "ProductFavouriteViewSet",
    "ProductImageViewSet",
    "ProductViewSet",
    "ProductReviewViewSet",
]
