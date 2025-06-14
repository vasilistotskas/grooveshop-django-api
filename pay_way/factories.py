import random

import factory
from django.apps import apps
from django.conf import settings
from factory.fuzzy import FuzzyDecimal
from faker import Faker

from pay_way.enum.pay_way import PayWayEnum
from pay_way.models import PayWay

fake = Faker()
available_languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]


class PayWayTranslationFactory(factory.django.DjangoModelFactory):
    language_code = factory.Iterator(available_languages)
    name = factory.Iterator([choice[0] for choice in PayWayEnum.choices])
    description = factory.LazyFunction(lambda: fake.paragraph())
    instructions = factory.LazyFunction(
        lambda: fake.paragraph(nb_sentences=3)
        if random.choice([True, False])
        else ""
    )
    master = factory.SubFactory("pay_way.factories.PayWayFactory")

    class Meta:
        model = apps.get_model("pay_way", "PayWayTranslation")
        django_get_or_create = ("language_code", "master")


def generate_stripe_config():
    return {
        "api_key": "sk_test_" + fake.lexify(text="?" * 24),
        "public_key": "pk_test_" + fake.lexify(text="?" * 24),
        "webhook_secret": "whsec_" + fake.lexify(text="?" * 24),
    }


def generate_paypal_config():
    return {
        "client_id": fake.uuid4(),
        "client_secret": fake.lexify(text="?" * 32),
        "environment": random.choice(["sandbox", "production"]),
    }


def generate_bank_transfer_config():
    return {
        "account_number": fake.numerify(text="##########"),
        "routing_number": fake.numerify(text="#########"),
        "bank_name": fake.company(),
        "account_holder": fake.name(),
    }


def generate_provider_data():
    providers = [
        ("stripe", True, False, generate_stripe_config),
        ("paypal", True, False, generate_paypal_config),
        ("bank_transfer", False, True, generate_bank_transfer_config),
        ("cash", False, False, lambda: None),
        ("", False, False, lambda: None),
    ]

    provider = random.choice(providers)
    return {
        "provider_code": provider[0],
        "is_online_payment": provider[1],
        "requires_confirmation": provider[2],
        "configuration": provider[3](),
    }


class PayWayFactory(factory.django.DjangoModelFactory):
    active = factory.Faker("boolean")
    cost = FuzzyDecimal(3, 10, 2)
    free_threshold = FuzzyDecimal(100, 200, 2)
    icon = factory.django.ImageField(
        filename="pay_way_icon.jpg",
        color=factory.Faker("color"),
        width=256,
        height=256,
    )
    provider_code = factory.LazyFunction(
        lambda: generate_provider_data()["provider_code"]
    )
    is_online_payment = factory.LazyFunction(
        lambda: generate_provider_data()["is_online_payment"]
    )
    requires_confirmation = factory.LazyFunction(
        lambda: generate_provider_data()["requires_confirmation"]
    )
    configuration = factory.LazyFunction(
        lambda: generate_provider_data()["configuration"]
    )

    class Meta:
        model = PayWay
        skip_postgeneration_save = True

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
        config = None
        if provider_code == "stripe":
            config = generate_stripe_config()
        elif provider_code == "paypal":
            config = generate_paypal_config()

        return cls.create(
            provider_code=provider_code,
            is_online_payment=True,
            requires_confirmation=False,
            configuration=config,
            **kwargs,
        )

    @classmethod
    def create_offline_payment(
        cls, provider_code="bank_transfer", requires_confirmation=True, **kwargs
    ):
        config = None
        if provider_code == "bank_transfer":
            config = generate_bank_transfer_config()

        return cls.create(
            provider_code=provider_code,
            is_online_payment=False,
            requires_confirmation=requires_confirmation,
            configuration=config,
            **kwargs,
        )
