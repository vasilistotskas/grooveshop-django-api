from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from parler_rest.serializers import TranslatableModelSerializer
from rest_framework import serializers

from core.api.schema import generate_schema_multi_lang
from core.utils.serializers import TranslatedFieldExtended
from product.enum.review import RateEnum, ReviewStatus
from product.models.product import Product
from product.models.review import ProductReview
from product.serializers.product import ProductSerializer
from tenant.celery import dispatch_on_commit
from user.serializers.account import UserPublicSerializer

User = get_user_model()


@extend_schema_field(generate_schema_multi_lang(ProductReview))
class TranslatedFieldsFieldExtend(TranslatedFieldExtended):
    pass


class ProductBriefSerializer(serializers.ModelSerializer[Product]):
    """Minimal product representation for use in list-level review serializer."""

    name = serializers.SerializerMethodField()
    main_image_path = serializers.ReadOnlyField()

    def get_name(self, obj) -> str | None:
        return obj.safe_translation_getter("name", any_language=True)

    class Meta:
        model = Product
        fields = ("id", "name", "slug", "main_image_path")


class ProductReviewSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer[ProductReview]
):
    translations = TranslatedFieldsFieldExtend(shared_model=ProductReview)
    # PUBLIC serializer: these endpoints serve anonymous readers, and
    # the account serializer carries email/phone/address/birth_date.
    user = UserPublicSerializer(read_only=True)
    product = ProductBriefSerializer(read_only=True)
    is_verified_purchase = serializers.BooleanField(read_only=True)

    class Meta:
        model = ProductReview
        fields = (
            "id",
            "product",
            "user",
            "rate",
            "is_verified_purchase",
            "status",
            "is_published",
            "created_at",
            "updated_at",
            "published_at",
            "uuid",
            "translations",
        )
        read_only_fields = (
            "id",
            "created_at",
            "updated_at",
            "published_at",
            "uuid",
        )


class ProductReviewDetailSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer[ProductReview]
):
    translations = TranslatedFieldsFieldExtend(shared_model=ProductReview)
    # PUBLIC serializer: these endpoints serve anonymous readers, and
    # the account serializer carries email/phone/address/birth_date.
    user = UserPublicSerializer(read_only=True)
    product = ProductSerializer(read_only=True)
    is_verified_purchase = serializers.BooleanField(read_only=True)

    class Meta:
        model = ProductReview
        fields = (
            "id",
            "product",
            "user",
            "rate",
            "is_verified_purchase",
            "status",
            "is_published",
            "created_at",
            "updated_at",
            "published_at",
            "uuid",
            "translations",
        )
        read_only_fields = (
            "id",
            "created_at",
            "updated_at",
            "published_at",
            "uuid",
        )


class ProductReviewWriteSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer[ProductReview]
):
    translations = TranslatedFieldsFieldExtend(shared_model=ProductReview)
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all())

    class Meta:
        model = ProductReview
        fields = (
            "product",
            "rate",
            "status",
            "is_published",
            "translations",
        )
        read_only_fields = ("status", "is_published")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # A review's product is fixed at creation. Allowing it to change on
        # update would let a user re-point an existing review at a different
        # product they never bought, bypassing the verified-purchase gate
        # (G0303). Make it read-only once the review exists.
        if self.instance is not None:
            self.fields["product"].read_only = True

    def validate_rate(self, value: int) -> int:
        valid_rates = [choice[0] for choice in RateEnum.choices]
        if value not in valid_rates:
            raise serializers.ValidationError(
                _("Invalid rate value."), code="invalid_rate"
            )
        return value

    def create(self, validated_data):
        user = self.context["request"].user
        validated_data["user"] = user
        return super().create(validated_data)

    def validate(self, attrs):
        user = self.context["request"].user
        product = attrs.get("product")

        if (
            self.instance is None
            and ProductReview.objects.filter(
                user=user, product=product
            ).exists()
        ):
            raise serializers.ValidationError(
                _("You have already reviewed this product."),
                code="already_reviewed",
            )

        return attrs

    def save(self, **kwargs):
        """Put a review the owner changed back in front of staff.

        The one place the rule lives, because it is the one write path
        for a shopper (create, PUT and PATCH all end here) and the only
        one that knows who is writing and what the new text is: parler
        pops the translations out of ``validated_data`` inside
        ``super().save()``, after the row is written, so neither
        ``update()`` nor the model's ``save()`` can compare the comment.
        The admin never reaches this serializer, so a staff edit does not
        reset anything.

        An owner's change to the rate or the comment of an approved or
        rejected review sends it back to ``NEW``, which hides it from the
        public list and the averages until it is approved again. An
        update that changes nothing leaves the status alone. Staff are
        emailed once per entry into ``NEW``: on create and on that reset.
        """
        created = self.instance is None
        resubmitted = not created and self._owner_changed_the_content()
        if resubmitted:
            kwargs["status"] = ReviewStatus.NEW
        review = super().save(**kwargs)
        if created or resubmitted:
            from product.tasks import send_review_pending_alert

            dispatch_on_commit(
                send_review_pending_alert,
                kwargs={"review_id": review.pk},
                robust=True,
            )
        return review

    def _owner_changed_the_content(self) -> bool:
        review = self.instance
        if review.status == ReviewStatus.NEW:
            return False
        if review.user_id != self.context["request"].user.pk:
            return False
        data = self.validated_data
        if "rate" in data and data["rate"] != review.rate:
            return True
        for language, fields in (data.get("translations") or {}).items():
            if "comment" not in fields:
                continue
            current = review.safe_translation_getter(
                "comment", language_code=language, any_language=False
            )
            if (fields["comment"] or "") != (current or ""):
                return True
        return False
