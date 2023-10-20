from typing import List

from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from phonenumber_field.modelfields import PhoneNumberField

from core.models import TimeStampMixinModel
from core.models import UUIDModel
from user.enum.address import FloorChoicesEnum
from user.enum.address import LocationChoicesEnum


class UserAddress(TimeStampMixinModel, UUIDModel):
    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(
        "user.UserAccount", related_name="user_address", on_delete=models.CASCADE
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
    floor = models.PositiveSmallIntegerField(
        _("Floor"),
        choices=FloorChoicesEnum.choices,
        null=True,
        blank=True,
        default=None,
    )
    location_type = models.PositiveSmallIntegerField(
        _("Location Type"),
        choices=LocationChoicesEnum.choices,
        null=True,
        blank=True,
        default=None,
    )
    phone = PhoneNumberField(_("Phone Number"), null=True, blank=True, default=None)
    mobile_phone = PhoneNumberField(_("Mobile Phone Number"))
    notes = models.CharField(
        _("Notes"), max_length=255, null=True, blank=True, default=None
    )
    is_main = models.BooleanField(_("Is Main"), default=False)

    class Meta(TypedModelMeta):
        verbose_name = _("User Address")
        verbose_name_plural = _("User Addresses")
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "is_main"],
                condition=models.Q(is_main=True),
                name="unique_main_address",
            )
        ]

    def __str__(self):
        return self.title

    @classmethod
    # Return type is a list of UserAddress
    def get_user_addresses(cls, user) -> List["UserAddress"]:
        return cls.objects.filter(user=user)

    @classmethod
    def get_main_address(cls, user) -> "UserAddress":
        return cls.objects.filter(user=user, is_main=True).first()

    @classmethod
    def get_user_address_count(cls, user) -> int:
        return cls.objects.filter(user=user).count()
