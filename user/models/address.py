from django.contrib.postgres.indexes import BTreeIndex, GinIndex
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from phonenumber_field.modelfields import PhoneNumberField

from core.enum import FloorChoicesEnum, LocationChoicesEnum
from core.models import TimeStampMixinModel, UUIDModel
from user.managers.address import UserAddressManager


class UserAddress(TimeStampMixinModel, UUIDModel):
    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(
        "user.UserAccount",
        related_name="addresses",
        on_delete=models.CASCADE,
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
        related_name="user_addresses",
        null=True,
        blank=True,
        default=None,
        on_delete=models.SET_NULL,
    )
    region = models.ForeignKey(
        "region.Region",
        related_name="user_addresses",
        null=True,
        blank=True,
        default=None,
        on_delete=models.SET_NULL,
    )
    floor = models.CharField(
        _("Floor"),
        max_length=50,
        choices=FloorChoicesEnum,
        blank=True,
        default="",
    )
    location_type = models.CharField(
        _("Location Type"),
        max_length=100,
        choices=LocationChoicesEnum,
        blank=True,
        default="",
    )
    phone = PhoneNumberField(
        _("Phone Number"), null=True, blank=True, default=None
    )
    mobile_phone = PhoneNumberField(_("Mobile Phone Number"))
    notes = models.CharField(_("Notes"), max_length=255, blank=True, default="")
    is_main = models.BooleanField(_("Is Main"), default=False)

    objects: UserAddressManager = UserAddressManager()

    class Meta(TypedModelMeta):
        verbose_name = _("User Address")
        verbose_name_plural = _("User Addresses")
        ordering = ["-is_main"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "is_main"],
                condition=models.Q(is_main=True),
                name="unique_main_address",
            )
        ]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(fields=["user"], name="user_address_user_ix"),
            BTreeIndex(fields=["is_main"], name="user_address_is_main_ix"),
            BTreeIndex(fields=["country"], name="user_address_country_ix"),
            BTreeIndex(fields=["region"], name="user_address_region_ix"),
            GinIndex(
                name="user_address_search_gin",
                fields=["title", "first_name", "last_name", "city"],
                opclasses=["gin_trgm_ops"] * 4,
            ),
        ]

    def __str__(self):
        return f"{self.title} - {self.first_name} {self.last_name}, {self.city}"

    def save(self, *args, **kwargs):
        if self.is_main:
            UserAddress.objects.filter(user=self.user, is_main=True).exclude(
                pk=self.id
            ).update(is_main=False)
        super().save(*args, **kwargs)

    def clean(self):
        if self.is_main:
            main_count = (
                UserAddress.objects.filter(user=self.user, is_main=True)
                .exclude(pk=self.pk)
                .count()
            )
            if main_count > 0:
                raise ValidationError(
                    _("There can only be one main address per user.")
                )

    @classmethod
    def get_user_addresses(cls, user):
        return cls.objects.filter(user=user)

    @classmethod
    def get_main_address(cls, user):
        return cls.objects.filter(user=user, is_main=True).first()

    @classmethod
    def get_user_address_count(cls, user):
        return cls.objects.filter(user=user).count()
