from django.contrib.postgres.indexes import BTreeIndex
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

from contact.managers import ContactManager, FeedbackManager
from core.models import TimeStampMixinModel, UUIDModel


class Contact(
    TimeStampMixinModel,
    UUIDModel,
):
    name = models.CharField(_("Name"), max_length=100)
    email = models.EmailField(_("Email"))
    message = models.TextField(_("Message"))
    # Optional context a B2B enquiry carries and a consumer one does
    # not. All three are blank-able because the platform's own form
    # asks for none of them: a merchant whose form does (an
    # engineering contractor quoting per project needs to know which
    # utility is asking, and to phone back) gets them structured
    # rather than buried in the message body.
    #
    # ``db_default``, not ``default``: the deploy applies migrations in
    # a PreSync hook, so the column exists while the PREVIOUS image is
    # still serving — and that image's INSERT does not list it. A
    # Django-level default is dropped from the DDL once the migration
    # ends, which would make every contact submission in the rollout
    # window a NOT NULL violation. Same reasoning as
    # ``BlogPost.click_score``.
    company = models.CharField(
        _("Company"), max_length=150, blank=True, db_default=""
    )
    phone = models.CharField(
        _("Phone"), max_length=30, blank=True, db_default=""
    )
    # FREE TEXT, not choices: the taxonomy belongs to the merchant's
    # own form ("Προσφορά έργου", "DeSET / ΑΠΕ", "Υποστήριξη"), which
    # is content in a page section, not a platform-wide enum. It is a
    # self-declared label on an anonymous submission and never an
    # authorization input.
    subject = models.CharField(
        _("Subject"), max_length=60, blank=True, db_default=""
    )

    objects: ContactManager = ContactManager()

    def __str__(self):
        return f"{self.name} <{self.email}>"

    def __repr__(self):
        return (
            f"{self.__class__.__name__}(name={self.name}, email={self.email})"
        )

    class Meta(TypedModelMeta):
        verbose_name = _("Contact")
        verbose_name_plural = _("Contacts")
        ordering = ["-created_at"]
        indexes = [
            # Both halves of the parent's pair are earned here: the list
            # orders by created_at and paginates it, `date_hierarchy`
            # range-scans it, RecentContactFilter issues four
            # `created_at__gte` variants, and the admin exposes a
            # RangeDateTimeFilter on updated_at as well.
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(fields=["email"], name="contact_email_ix"),
        ]


class FeedbackCategory(models.TextChoices):
    GENERAL = "general", _("General")
    WEBSITE = "website", _("Website & UX")
    PRODUCTS = "products", _("Products")
    DELIVERY = "delivery", _("Delivery")
    SUPPORT = "support", _("Customer support")
    OTHER = "other", _("Other")


class Feedback(
    TimeStampMixinModel,
    UUIDModel,
):
    name = models.CharField(_("Name"), max_length=100, blank=True, default="")
    email = models.EmailField(_("Email"), blank=True, default="")
    rating = models.PositiveSmallIntegerField(
        _("Rating"), validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    category = models.CharField(
        _("Category"),
        max_length=20,
        choices=FeedbackCategory.choices,
        default=FeedbackCategory.GENERAL,
    )
    message = models.TextField(_("Message"))

    objects: FeedbackManager = FeedbackManager()

    def __str__(self):
        name = self.name or str(_("Anonymous"))
        return f"{self.get_category_display()} · {self.rating}★ · {name}"

    class Meta(TypedModelMeta):
        verbose_name = _("Feedback")
        verbose_name_plural = _("Feedback")
        ordering = ["-created_at"]
        indexes = [
            # created_at only: the list orders by it and `date_hierarchy`
            # range-scans it. updated_at is shown in the admin but never
            # sorted or filtered, and an index nobody reads is a write
            # cost on every row.
            BTreeIndex(fields=["created_at"], name="feedback_created_at_ix"),
            BTreeIndex(fields=["category"], name="feedback_category_ix"),
            BTreeIndex(fields=["rating"], name="feedback_rating_ix"),
        ]
