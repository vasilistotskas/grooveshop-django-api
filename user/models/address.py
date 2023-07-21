from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models import TimeStampMixinModel
from core.models import UUIDModel
from user.enum.address import FloorChoicesEnum
from user.enum.address import LocationChoicesEnum


class UserAddress(TimeStampMixinModel, UUIDModel):
    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(
        "user.UserAccount", related_name="address_user", on_delete=models.CASCADE
    )
    title = models.CharField(_("Title"), max_length=255)
    first_name = models.CharField(_("First Name"), max_length=255)
    last_name = models.CharField(_("Last Name"), max_length=255)
    street = models.CharField(_("Street"), max_length=255)
    street_number = models.CharField(_("Street Number"), max_length=255)
    city = models.CharField(_("City"), max_length=255)
    zipcode = models.CharField(_("Zip Code"), max_length=255)
    country = models.ForeignKey(
        "country.Country",
        related_name="address_country",
        null=True,
        blank=True,
        default=None,
        on_delete=models.SET_NULL,
    )
    region = models.ForeignKey(
        "region.Region",
        related_name="address_region",
        null=True,
        blank=True,
        default=None,
        on_delete=models.SET_NULL,
    )
    floor = models.CharField(
        _("Floor"),
        max_length=50,
        choices=FloorChoicesEnum.choices(),
        null=True,
        blank=True,
        default=None,
    )
    location_type = models.CharField(
        _("Location Type"),
        max_length=100,
        choices=LocationChoicesEnum.choices(),
        null=True,
        blank=True,
        default=None,
    )
    phone = models.CharField(max_length=255, null=True, blank=True, default=None)
    mobile_phone = models.CharField(max_length=255, null=True, blank=True, default=None)
    notes = models.CharField(max_length=255, null=True, blank=True, default=None)
    is_main = models.BooleanField(default=False)

    class Meta:
        verbose_name = _("User Address")
        verbose_name_plural = _("User Addresses")
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    @classmethod
    def get_user_addresses(cls, user) -> models.QuerySet:
        return cls.objects.filter(user=user)

    @classmethod
    def get_main_address(cls, user) -> models.QuerySet:
        return cls.objects.filter(user=user, is_main=True).first()

    @classmethod
    def get_user_address_count(cls, user) -> int:
        return cls.objects.filter(user=user).count()
