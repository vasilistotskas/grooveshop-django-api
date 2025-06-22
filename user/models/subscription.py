from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from parler.models import TranslatableModel, TranslatedFields

from core.models import TimeStampMixinModel, UUIDModel


class SubscriptionTopic(TranslatableModel, TimeStampMixinModel, UUIDModel):
    class TopicCategory(models.TextChoices):
        MARKETING = "MARKETING", _("Marketing Campaigns")
        PRODUCT = "PRODUCT", _("Product Updates")
        ACCOUNT = "ACCOUNT", _("Account Updates")
        SYSTEM = "SYSTEM", _("System Notifications")
        NEWSLETTER = "NEWSLETTER", _("Newsletter")
        PROMOTIONAL = "PROMOTIONAL", _("Promotional")
        OTHER = "OTHER", _("Other")

    slug = models.SlugField(
        _("Slug"),
        max_length=50,
        unique=True,
        help_text=_(
            "Unique identifier for the topic (e.g., 'weekly-newsletter')"
        ),
    )
    category = models.CharField(
        _("Category"),
        max_length=20,
        choices=TopicCategory,
        default=TopicCategory.OTHER,
        help_text=_("Category of the subscription topic"),
    )
    is_active = models.BooleanField(
        _("Active"),
        default=True,
        help_text=_(
            "Whether this topic is currently available for subscription"
        ),
    )
    is_default = models.BooleanField(
        _("Default Subscription"),
        default=False,
        help_text=_(
            "Whether new users are automatically subscribed to this topic"
        ),
    )
    requires_confirmation = models.BooleanField(
        _("Requires Confirmation"),
        default=False,
        help_text=_(
            "Whether subscription to this topic requires email confirmation"
        ),
    )
    translations = TranslatedFields(
        name=models.CharField(
            _("Name"),
            max_length=100,
            help_text=_("Human-readable name for the topic"),
        ),
        description=models.TextField(
            _("Description"),
            blank=True,
            help_text=_(
                "Detailed description of what this subscription includes"
            ),
        ),
    )

    class Meta(TypedModelMeta):
        verbose_name = _("Subscription Topic")
        verbose_name_plural = _("Subscription Topics")
        ordering = ["-created_at"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(fields=["slug"], name="sub_topic_slug_ix"),
            BTreeIndex(fields=["is_active"], name="sub_topic_active_ix"),
            BTreeIndex(fields=["category"], name="sub_topic_category_ix"),
        ]

    def __str__(self):
        name = (
            self.safe_translation_getter("name", any_language=True)
            or "Unnamed Topic"
        )
        return f"{name} ({self.category})"


class UserSubscription(TimeStampMixinModel, UUIDModel):
    class SubscriptionStatus(models.TextChoices):
        ACTIVE = "ACTIVE", _("Active")
        PENDING = "PENDING", _("Pending Confirmation")
        UNSUBSCRIBED = "UNSUBSCRIBED", _("Unsubscribed")
        BOUNCED = "BOUNCED", _("Bounced")

    user = models.ForeignKey(
        "user.UserAccount",
        on_delete=models.CASCADE,
        related_name="subscriptions",
        verbose_name=_("User"),
    )
    topic = models.ForeignKey(
        SubscriptionTopic,
        on_delete=models.CASCADE,
        related_name="subscribers",
        verbose_name=_("Topic"),
    )
    status = models.CharField(
        _("Status"),
        max_length=20,
        choices=SubscriptionStatus,
        default=SubscriptionStatus.ACTIVE,
    )
    subscribed_at = models.DateTimeField(_("Subscribed At"), auto_now_add=True)
    unsubscribed_at = models.DateTimeField(
        _("Unsubscribed At"), null=True, blank=True
    )
    confirmation_token = models.CharField(
        _("Confirmation Token"),
        max_length=64,
        blank=True,
        help_text=_("Token for email confirmation if required"),
    )
    metadata = models.JSONField(
        _("Metadata"),
        default=dict,
        blank=True,
        help_text=_("Additional subscription preferences or data"),
    )

    class Meta(TypedModelMeta):
        verbose_name = _("User Subscription")
        verbose_name_plural = _("User Subscriptions")
        unique_together = [["user", "topic"]]
        ordering = ["-created_at"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(
                fields=["user", "status"], name="user_sub_user_status_ix"
            ),
            BTreeIndex(
                fields=["topic", "status"], name="user_sub_topic_status_ix"
            ),
            BTreeIndex(fields=["status"], name="user_sub_status_ix"),
            BTreeIndex(fields=["confirmation_token"], name="user_sub_token_ix"),
        ]

    def __str__(self):
        return f"{self.user} - {self.topic} ({self.status})"

    def unsubscribe(self):
        self.status = self.SubscriptionStatus.UNSUBSCRIBED
        self.unsubscribed_at = timezone.now()
        self.save(update_fields=["status", "unsubscribed_at", "updated_at"])

    def activate(self):
        self.status = self.SubscriptionStatus.ACTIVE
        self.confirmation_token = ""
        self.save(update_fields=["status", "confirmation_token", "updated_at"])
