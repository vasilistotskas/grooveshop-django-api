from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from blog.serializers.comment import BlogCommentSerializer
from core.api.serializers import BaseExpandSerializer
from order.serializers.order import OrderSerializer
from product.serializers.favourite import ProductFavouriteSerializer
from product.serializers.review import ProductReviewSerializer
from user.models import UserAccount
from user.serializers.address import UserAddressSerializer


class UserAccountDetailsSerializer(BaseExpandSerializer):
    favourite_products = serializers.SerializerMethodField("get_favourite_products")
    orders = serializers.SerializerMethodField("get_orders")
    product_reviews = serializers.SerializerMethodField("get_product_reviews")
    user_addresses = serializers.SerializerMethodField("get_user_addresses")
    blog_post_comments = serializers.SerializerMethodField("get_blog_post_comments")
    blog_liked_posts = serializers.SerializerMethodField("get_blog_liked_posts")
    blog_liked_comments = serializers.SerializerMethodField("get_blog_liked_comments")

    @extend_schema_field(ProductFavouriteSerializer)
    def get_favourite_products(self, user_account: UserAccount):
        return ProductFavouriteSerializer(
            user_account.user_product_favourite, many=True, context=self.context
        ).data

    @extend_schema_field(OrderSerializer)
    def get_orders(self, user_account: UserAccount):
        return OrderSerializer(
            user_account.user_order, many=True, context=self.context
        ).data

    @extend_schema_field(ProductReviewSerializer)
    def get_product_reviews(self, user_account: UserAccount):
        return ProductReviewSerializer(
            user_account.product_reviews, many=True, context=self.context
        ).data

    @extend_schema_field(UserAddressSerializer)
    def get_user_addresses(self, user_account: UserAccount):
        return UserAddressSerializer(
            user_account.user_address, many=True, context=self.context
        ).data

    @extend_schema_field(BlogCommentSerializer)
    def get_blog_post_comments(self, user_account: UserAccount):
        return BlogCommentSerializer(
            user_account.blog_comment_user, many=True, context=self.context
        ).data

    def get_blog_liked_posts(self, user_account: UserAccount) -> list[int]:
        liked_posts_ids = user_account.blog_post_likes.values_list("id", flat=True)
        return list(liked_posts_ids)

    def get_blog_liked_comments(self, user_account: UserAccount) -> list[int]:
        liked_comments_ids = user_account.blog_comment_likes.values_list(
            "id", flat=True
        )
        return list(liked_comments_ids)

    class Meta:
        model = UserAccount
        fields = (
            "favourite_products",
            "orders",
            "product_reviews",
            "user_addresses",
            "blog_post_comments",
            "blog_liked_posts",
            "blog_liked_comments",
        )
