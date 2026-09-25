from django.contrib.postgres.indexes import BTreeIndex
from django.core.validators import RegexValidator
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from parler.models import TranslatableModel, TranslatedFields

from core.models import SortableModel, TimeStampMixinModel, UUIDModel
from core.validators.address import validate_postal_code_pattern
from country.managers import CountryManager


class Country(TranslatableModel, TimeStampMixinModel, SortableModel, UUIDModel):
    alpha_2 = models.CharField(
        _("Country Code Alpha 2"),
        primary_key=True,
        unique=True,
        max_length=2,
        validators=[
            RegexValidator(
                regex="^[A-Z]{2}$",
                message=_("Enter a valid 2-letter country code."),
            )
        ],
    )
    alpha_3 = models.CharField(
        _("Country Code Alpha 3"),
        unique=True,
        max_length=3,
        validators=[
            RegexValidator(
                regex="^[A-Z]{3}$",
                message=_("Enter a valid 3-letter country code."),
            )
        ],
    )
    iso_cc = models.PositiveSmallIntegerField(
        _("ISO Country Code"), blank=True, null=True, unique=True
    )
    phone_code = models.PositiveSmallIntegerField(
        # NOT unique: calling codes are shared across countries (+1 US/CA,
        # +7 RU/KZ), so a unique constraint made those countries
        # unrepresentable (G0118).
        _("Phone Code"),
        blank=True,
        null=True,
    )
    image_flag = models.ImageField(
        _("Image Flag"), blank=True, null=True, upload_to="uploads/country/"
    )
    # The country's postcode format, verbatim from Google's Address Data
    # Service (``zip`` and the first ``zipex`` of
    # https://www.gstatic.com/chrome/autofill/libaddressinput/chromium-i18n/ssl-address/data/<CC>).
    # One row serves both repos: Django validates every address write
    # against it (``core.validators.address``) and the storefront reads
    # it from the countries API, so the checkout's inline check and the
    # server's cannot disagree. Blank means no format rule, only
    # "required". ``db_default`` because the PreSync migration adds the
    # column while the previous image, whose INSERT omits it, still serves.
    postal_code_pattern = models.CharField(
        _("Postal Code Pattern"),
        max_length=1000,
        blank=True,
        default="",
        db_default="",
        validators=[validate_postal_code_pattern],
        help_text=_(
            "Regular expression a whole postcode must match, e.g. "
            "\\d{3} ?\\d{2} for Greece. Blank disables the format check."
        ),
    )
    postal_code_example = models.CharField(
        _("Postal Code Example"),
        max_length=50,
        blank=True,
        default="",
        db_default="",
        help_text=_("A valid postcode shown to shoppers, e.g. 151 24."),
    )
    translations = TranslatedFields(
        name=models.CharField(_("Name"), max_length=100, blank=True, null=True)
    )

    objects: CountryManager = CountryManager()

    class Meta(TypedModelMeta):
        ordering = ["sort_order"]
        verbose_name = _("Country")
        verbose_name_plural = _("Countries")
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            *SortableModel.Meta.indexes,
            BTreeIndex(fields=["alpha_2"], name="country_alpha_2_ix"),
            BTreeIndex(fields=["alpha_3"], name="country_alpha_3_ix"),
            BTreeIndex(fields=["iso_cc"], name="country_iso_cc_ix"),
            BTreeIndex(fields=["phone_code"], name="country_phone_code_ix"),
        ]

    def __str__(self):
        return self.safe_translation_getter("name", any_language=True) or ""

    def save(self, *args, **kwargs):
        self.alpha_2 = self.alpha_2.upper()
        self.alpha_3 = self.alpha_3.upper()
        super().save(*args, **kwargs)

    def get_ordering_queryset(self):
        return Country.objects.all()

    @property
    def main_image_path(self) -> str:
        from core.utils.image_paths import image_to_media_path

        return image_to_media_path(self.image_flag)
