import random

import factory
from django.apps import apps
from django.conf import settings
from factory.fuzzy import FuzzyDecimal
from faker import Faker

from pay_way.enum.pay_way import PayWayEnum
from pay_way.models import PayWay, PayWayShippingExclusion
from shipping.enum import ShippingKind

fake = Faker()
available_languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]


class PayWayTranslationFactory(factory.django.DjangoModelFactory):
    language_code = factory.Iterator(available_languages)
    name = factory.Iterator([choice.value for choice in PayWayEnum])
    description = factory.Faker(
        "random_element",
        elements=[
            "Fast and secure payment processing for all major credit and debit cards.",
            "Pay safely with your PayPal account or credit card through PayPal.",
            "Secure payment processing powered by Stripe. All major cards accepted.",
            "Transfer funds directly from your bank account. Processing takes 1-3 business days.",
            "Pay with cash when your order is delivered to your doorstep.",
            "Quick and easy payment using Apple Pay on your iPhone or iPad.",
            "Pay securely with Google Pay using your Android device or browser.",
            "International wire transfers accepted. Contact us for bank details.",
        ],
    )
    instructions = factory.Faker(
        "random_element",
        elements=[
            "Enter your card details at checkout. Your payment will be processed securely.",
            "Click the PayPal button and log in to your account to complete the payment.",
            "Use your bank account number and routing number to set up the transfer.",
            "Have cash ready when the delivery driver arrives. Credit cards not accepted for COD.",
            "Select Apple Pay at checkout and authenticate with Face ID or Touch ID.",
            "Tap Google Pay at checkout and confirm with your saved payment method.",
            "Contact our billing department for wire transfer instructions.",
            "",
        ],
    )
    master = factory.SubFactory("pay_way.factories.PayWayFactory")

    class Meta:
        model = apps.get_model("pay_way", "PayWayTranslation")
        django_get_or_create = ("language_code", "master", "name")


def generate_stripe_config():
    return {
        "api_key": "sk_test_" + fake.lexify(text="?" * 24),
        "public_key": "pk_test_" + fake.lexify(text="?" * 24),
        "webhook_secret": "whsec_" + fake.lexify(text="?" * 24),
    }


def generate_bank_transfer_config():
    return {
        "account_number": fake.numerify(text="##########"),
        "routing_number": fake.numerify(text="#########"),
        "bank_name": fake.company(),
        "account_holder": fake.name(),
    }


def generate_provider_data():
    """Draw one coherent OFFLINE pay-way identity.

    Only offline codes are drawn. The two providers this platform can
    actually charge through — "stripe" and "viva_wallet" — are gated by
    ``PayWayService.is_provider_configured()`` on tenant-only credentials
    with no platform fallback, so a randomly-drawn one is invisible to
    anonymous list/retrieve queries unless a tenant key happens to be
    bound: a call site that just wants "some usable pay-way" must not
    flake depending on the random seed. Tests that want an online
    pay-way call ``create_online_payment`` and mock the credentials.
    """
    providers = [
        ("bank_transfer", True, generate_bank_transfer_config),
        ("cash", False, lambda: None),
        ("", False, lambda: None),
    ]

    provider_code, requires_confirmation, build_config = random.choice(
        providers
    )
    return {
        "provider_code": provider_code,
        "requires_confirmation": requires_confirmation,
        "configuration": build_config(),
    }


class PayWayFactory(factory.django.DjangoModelFactory):
    # Default to a *usable* (active) pay-way. Order creation now rejects
    # inactive pay-ways, so a random ``active`` default silently flaked
    # any checkout test that omitted it. Tests exercising the inactive
    # path must set ``active=False`` explicitly.
    active = True
    cost = FuzzyDecimal(3, 10, 2)
    free_threshold = FuzzyDecimal(100, 200, 2)
    icon = factory.django.ImageField(
        filename="pay_way_icon.jpg",
        color=factory.Faker("color"),
        width=256,
        height=256,
    )
    # ONE draw feeds all three fields. They were three independent
    # LazyFunction calls into generate_provider_data(), which handed out
    # incoherent rows — a "cash" pay-way carrying a bank-transfer
    # configuration, or an offline code marked is_online_payment=True
    # (which then failed at checkout with "Unknown payment provider").
    provider_data = factory.LazyFunction(generate_provider_data)
    provider_code = factory.LazyAttribute(
        lambda o: o.provider_data["provider_code"]
    )
    is_online_payment = False
    requires_confirmation = factory.LazyAttribute(
        lambda o: o.provider_data["requires_confirmation"]
    )
    configuration = factory.LazyAttribute(
        lambda o: o.provider_data["configuration"]
    )

    class Meta:
        model = PayWay
        skip_postgeneration_save = True
        # Helper value for the declarations above; never a model field.
        exclude = ("provider_data",)

    @factory.post_generation
    def translations(self, create, extracted, **kwargs):
        if not create:
            return

        translations = extracted or [
            PayWayTranslationFactory(language_code=lang, master=self)
            for lang in available_languages
        ]

        for translation in translations:
            translation.master = self
            translation.save()

    @classmethod
    def create_online_payment(cls, provider_code="stripe", **kwargs):
        # "stripe" is the only online code whose credential shape this
        # factory knows; any other gets no configuration unless the
        # caller passes one.
        kwargs.setdefault(
            "configuration",
            generate_stripe_config() if provider_code == "stripe" else None,
        )
        kwargs.setdefault("active", True)
        kwargs.setdefault("requires_confirmation", False)
        return cls.create(
            provider_code=provider_code,
            is_online_payment=True,
            **kwargs,
        )

    @classmethod
    def create_offline_payment(
        cls, provider_code="bank_transfer", requires_confirmation=True, **kwargs
    ):
        kwargs.setdefault(
            "configuration",
            generate_bank_transfer_config()
            if provider_code == "bank_transfer"
            else None,
        )
        kwargs.setdefault("active", True)
        return cls.create(
            provider_code=provider_code,
            is_online_payment=False,
            requires_confirmation=requires_confirmation,
            **kwargs,
        )


class PayWayShippingExclusionFactory(factory.django.DjangoModelFactory):
    pay_way = factory.SubFactory(PayWayFactory)
    shipping_provider = factory.SubFactory(
        "shipping.factories.ShippingProviderFactory"
    )
    shipping_kind = factory.Iterator([kind.value for kind in ShippingKind])
    note = ""

    class Meta:
        model = PayWayShippingExclusion
        # Match the unique constraint so re-using the same triple
        # under -n auto reuses the existing row instead of crashing.
        django_get_or_create = (
            "pay_way",
            "shipping_provider",
            "shipping_kind",
        )
