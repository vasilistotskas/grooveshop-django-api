from datetime import timedelta

from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone
from django.utils.crypto import constant_time_compare, get_random_string
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from parler.models import TranslatableModel, TranslatedFields

from core.models import TimeStampMixinModel, UUIDModel
from user.managers.subscription import (
    SubscriptionTopicManager,
    UserSubscriptionManager,
)

# How long an emailed confirmation link stays usable. Measured from
# ``UserSubscription.confirmation_sent_at``, which every re-arm resets,
# so a visitor who asks again gets a fresh week.
CONFIRMATION_TTL = timedelta(days=7)


class SubscriptionTopic(TranslatableModel, TimeStampMixinModel, UUIDModel):
    class TopicCategory(models.TextChoices):
        MARKETING = "MARKETING", _("Marketing Campaigns")
        PRODUCT = "PRODUCT", _("Product Updates")
        ACCOUNT = "ACCOUNT", _("Account Updates")
        SYSTEM = "SYSTEM", _("System Notifications")
        NEWSLETTER = "NEWSLETTER", _("Newsletter")
        PROMOTIONAL = "PROMOTIONAL", _("Promotional")
        OTHER = "OTHER", _("Other")

    # The ONLY categories a new account may be subscribed to without
    # asking — an ALLOWLIST, deliberately. Account and system notices are
    # service mail about the account itself. Everything else is, or may
    # be, direct marketing under ePrivacy art. 13: "Product Updates" is a
    # promotion of the store's goods, "Other" says nothing about what it
    # sends, and marketing/newsletter/promotional are marketing by name.
    # A subscription to any of those needs the recipient's own
    # affirmative act (GDPR art. 4(11) and 7), so ``is_default`` on such
    # a topic never auto-subscribes (``user.signals.
    # create_default_subscriptions``). A category added to the enum later
    # stays out until someone decides it belongs here.
    AUTO_SUBSCRIBE_CATEGORIES = frozenset(
        {
            TopicCategory.ACCOUNT,
            TopicCategory.SYSTEM,
        }
    )

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
            "Account and system topics: new users are subscribed "
            "automatically. Newsletter topic: the one the storefront "
            "newsletter form subscribes visitors to (at most one active). "
            "Topics of any other category are never subscribed "
            "automatically — they need the recipient's consent."
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

    objects: SubscriptionTopicManager = SubscriptionTopicManager()

    class Meta(TypedModelMeta):
        verbose_name = _("Subscription Topic")
        verbose_name_plural = _("Subscription Topics")
        ordering = ["-created_at"]
        constraints = [
            # The storefront newsletter form subscribes to "the tenant's
            # default newsletter topic". Two would make that ambiguous,
            # so the database holds it to one. An EXPRESSION, not
            # ``fields=``: DRF turns a single-field conditional
            # constraint into a UniqueValidator on ``category`` alone,
            # which would refuse every further newsletter-category
            # topic, default or not. The write serializer checks the
            # real rule (``SubscriptionTopicWriteSerializer.validate``).
            models.UniqueConstraint(
                "category",
                condition=models.Q(
                    category="NEWSLETTER", is_default=True, is_active=True
                ),
                name="sub_topic_one_default_newsletter",
                violation_error_message=_(
                    "Only one active newsletter topic can be the default."
                ),
            ),
        ]
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
    """A recipient's subscription to a topic — an account's, or a guest's.

    A guest row (``user`` null) is keyed by ``email``; it is created by
    the storefront newsletter form and always starts PENDING (double
    opt-in). It is attached to an account once that account verifies
    the same address (``user.utils.subscription.claim_guest_subscriptions``).

    The ``consent_*`` fields are the proof of consent for a form
    subscription: the exact sentence the visitor agreed to, in the
    language it was shown, with the network identity and the time it
    was given. ``confirmed_*`` records the double opt-in click.
    """

    class SubscriptionStatus(models.TextChoices):
        ACTIVE = "ACTIVE", _("Active")
        PENDING = "PENDING", _("Pending Confirmation")
        UNSUBSCRIBED = "UNSUBSCRIBED", _("Unsubscribed")
        BOUNCED = "BOUNCED", _("Bounced")

    class Source(models.TextChoices):
        NEWSLETTER_FORM = "NEWSLETTER_FORM", _("Newsletter form")
        ACCOUNT = "ACCOUNT", _("Account")
        SIGNUP = "SIGNUP", _("Signup")

    user = models.ForeignKey(
        "user.UserAccount",
        on_delete=models.CASCADE,
        related_name="subscriptions",
        verbose_name=_("User"),
        null=True,
        blank=True,
    )
    email = models.EmailField(
        _("Email"),
        blank=True,
        default="",
        help_text=_(
            "The address submitted on the newsletter form. The recipient "
            "of a guest subscription; an account's subscription mails "
            "the account's own address."
        ),
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
    source = models.CharField(
        _("Source"),
        max_length=20,
        choices=Source,
        default=Source.ACCOUNT,
        help_text=_("Where the subscription was made."),
    )
    language = models.CharField(
        _("Language"),
        max_length=10,
        blank=True,
        default="",
        help_text=_(
            "Language the subscription was made in; its emails use it."
        ),
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
    confirmation_sent_at = models.DateTimeField(
        _("Confirmation Sent At"),
        null=True,
        blank=True,
        help_text=_(
            "When the current confirmation link was issued. The link "
            "expires a week later."
        ),
    )
    confirmed_at = models.DateTimeField(
        _("Confirmed At"), null=True, blank=True
    )
    confirmed_ip = models.GenericIPAddressField(
        _("Confirmed From IP"), null=True, blank=True
    )
    consent_text = models.TextField(
        _("Consent Text"),
        blank=True,
        default="",
        help_text=_("The exact consent sentence shown to the subscriber."),
    )
    consent_ip = models.GenericIPAddressField(
        _("Consent IP"), null=True, blank=True
    )
    consent_user_agent = models.CharField(
        _("Consent User Agent"), max_length=512, blank=True, default=""
    )
    consented_at = models.DateTimeField(
        _("Consented At"),
        null=True,
        blank=True,
        help_text=_("When the subscriber submitted the consent."),
    )
    metadata = models.JSONField(
        _("Metadata"),
        default=dict,
        blank=True,
        help_text=_("Additional subscription preferences or data"),
    )

    objects: UserSubscriptionManager = UserSubscriptionManager()

    class Meta(TypedModelMeta):
        verbose_name = _("User Subscription")
        verbose_name_plural = _("User Subscriptions")
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "topic"],
                condition=models.Q(user__isnull=False),
                name="user_sub_unique_user_topic",
            ),
            models.UniqueConstraint(
                Lower("email"),
                "topic",
                condition=models.Q(user__isnull=True),
                name="user_sub_unique_guest_email_topic",
            ),
            models.CheckConstraint(
                condition=models.Q(user__isnull=False) | ~models.Q(email=""),
                name="user_sub_has_recipient",
            ),
        ]
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
        return f"{self.recipient_email} - {self.topic} ({self.status})"

    @property
    def recipient_email(self) -> str:
        """The address this subscription's mail goes to."""
        return self.user.email if self.user_id else self.email

    @property
    def confirmation_expired(self) -> bool:
        sent_at = self.confirmation_sent_at
        return sent_at is None or timezone.now() - sent_at > CONFIRMATION_TTL

    def arm_confirmation(self) -> None:
        """Put the row in PENDING with a fresh confirmation link.

        Does not save: callers set their own fields alongside and save
        once. The confirmation email is theirs to dispatch.
        """
        self.status = self.SubscriptionStatus.PENDING
        self.confirmation_token = get_random_string(64)
        self.confirmation_sent_at = timezone.now()
        self.confirmed_at = None
        self.confirmed_ip = None
        self.unsubscribed_at = None

    def unsubscribe(self):
        self.status = self.SubscriptionStatus.UNSUBSCRIBED
        self.unsubscribed_at = timezone.now()
        self.save(update_fields=["status", "unsubscribed_at", "updated_at"])

    def confirm(self, token: str, *, ip: str | None) -> ConfirmOutcome:
        """The double opt-in click: the ONE rule for confirming.

        Every path that confirms a subscription goes through here (the
        emailed-link view and the signed-in account's own confirm), so
        they cannot disagree about what a valid confirmation is: the row
        is PENDING, the token presented is its non-empty token, and the
        link is no older than ``CONFIRMATION_TTL``. On success the row
        turns ACTIVE with the time and — as far as it can be proven —
        the IP of the confirmation.
        """
        if (
            self.status != self.SubscriptionStatus.PENDING
            or not token
            or not self.confirmation_token
            or not constant_time_compare(token, self.confirmation_token)
        ):
            return ConfirmOutcome.INVALID
        if self.confirmation_expired:
            return ConfirmOutcome.EXPIRED
        self.status = self.SubscriptionStatus.ACTIVE
        self.confirmation_token = ""
        self.confirmed_at = timezone.now()
        self.confirmed_ip = ip
        self.save(
            update_fields=[
                "status",
                "confirmation_token",
                "confirmed_at",
                "confirmed_ip",
                "updated_at",
            ]
        )
        return ConfirmOutcome.CONFIRMED


class ConfirmOutcome(models.TextChoices):
    """What ``UserSubscription.confirm`` did."""

    CONFIRMED = "confirmed", _("Confirmed")
    INVALID = "invalid", _("Invalid")
    EXPIRED = "expired", _("Expired")
