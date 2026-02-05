import importlib

import factory
from django.apps import apps
from django.conf import settings
from faker import Faker

from devtools.factories import CustomDjangoModelFactory
from product.factories.image import ProductImageFactory
from product.factories.review import ProductReviewFactory
from product.models.product import Product
from tag.factories.tagged_item import TaggedProductFactory

fake = Faker()

available_languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]


def get_or_create_category():
    if apps.get_model("product", "ProductCategory").objects.exists():
        return (
            apps.get_model("product", "ProductCategory")
            .objects.order_by("?")
            .first()
        )
    else:
        category_factory_module = importlib.import_module(
            "product.factories.category"
        )
        category_factory_class = category_factory_module.ProductCategoryFactory
        return category_factory_class.create()


def get_or_create_vat():
    if apps.get_model("vat", "Vat").objects.exists():
        return apps.get_model("vat", "Vat").objects.order_by("?").first()
    else:
        vat_factory_module = importlib.import_module("vat.factories")
        vat_factory_class = vat_factory_module.VatFactory
        return vat_factory_class.create()


class ProductTranslationFactory(factory.django.DjangoModelFactory):
    language_code = factory.Iterator(available_languages)
    name = factory.Faker(
        "random_element",
        elements=[
            "Wireless Bluetooth Headphones",
            "Stainless Steel Water Bottle",
            "Organic Cotton T-Shirt",
            "Yoga Mat with Carrying Strap",
            "LED Desk Lamp",
            "Ceramic Coffee Mug Set",
            "Portable Phone Charger",
            "Running Shoes",
            "Leather Wallet",
            "Smart Watch",
            "Kitchen Knife Set",
            "Backpack with Laptop Compartment",
            "Sunglasses",
            "Bamboo Cutting Board",
            "Wireless Mouse",
            "Essential Oil Diffuser",
            "Stainless Steel Cookware Set",
            "Memory Foam Pillow",
            "Resistance Bands",
            "Plant-Based Protein Powder",
            "Wireless Earbuds",
            "Throw Blanket",
            "Canvas Tote Bag",
            "Electric Kettle",
            "Face Moisturizer",
            "Hand Soap Set",
            "Scented Candles",
            "Journal Notebook",
            "Insulated Lunch Box",
            "Sports Bottle",
            "Compression Socks",
            "Gaming Keyboard",
            "USB-C Cable",
            "Phone Case",
            "Desk Organizer",
            "Adjustable Dumbbell Set",
            "Air Purifier",
            "Night Light",
            "Reusable Shopping Bags",
            "Wireless Charging Pad",
            "Fitness Tracker",
            "Wool Socks",
            "Reversible Belt",
            "Tablet Stand",
            "Portable Speaker",
            "Bath Towel Set",
            "Non-Stick Frying Pan",
            "Reading Glasses",
            "Hand Cream",
            "Multivitamin Supplements",
            "Electric Toothbrush",
        ],
    )
    description = factory.Faker("text", max_nb_chars=500)
    master = factory.SubFactory("product.factories.product.ProductFactory")

    class Meta:
        model = apps.get_model("product", "ProductTranslation")
        django_get_or_create = ("language_code", "master")


class ProductFactory(CustomDjangoModelFactory):
    auto_translations = False

    unique_model_fields = [
        ("slug", lambda: fake.slug()),
    ]

    sku = factory.Faker("ean", length=13)
    category = factory.LazyFunction(get_or_create_category)
    price = factory.Faker(
        "pydecimal",
        left_digits=3,
        right_digits=2,
        positive=True,
        min_value=5,
        max_value=999,
    )
    active = factory.Faker("pybool", truth_probability=85)
    stock = factory.Faker("random_int", min=0, max=250)
    discount_percent = factory.Faker(
        "random_element", elements=[0, 0, 0, 5, 10, 15, 20, 25, 30, 50]
    )
    vat = factory.LazyFunction(get_or_create_vat)
    view_count = factory.Faker("random_int", min=0, max=5000)
    weight = factory.Faker(
        "pydecimal",
        left_digits=2,
        right_digits=2,
        positive=True,
        min_value=0.1,
        max_value=99,
    )

    class Meta:
        model = Product
        django_get_or_create = ("slug",)
        skip_postgeneration_save = True

    @factory.post_generation
    def num_images(self, create, extracted, **kwargs):
        if not create:
            return

        if extracted:
            ProductImageFactory.create_batch(extracted, product=self)

    @factory.post_generation
    def num_reviews(self, create, extracted, **kwargs):
        if not create:
            return

        if extracted:
            ProductReviewFactory.create_batch(extracted, product=self)

    @factory.post_generation
    def num_tags(self, create, extracted, **kwargs):
        if not create:
            return

        if extracted:
            TaggedProductFactory.create_batch(extracted, content_object=self)

    @factory.post_generation
    def translations(self, create, extracted, **kwargs):
        if not create:
            return

        translations = extracted or [
            ProductTranslationFactory(language_code=lang, master=self)
            for lang in available_languages
        ]

        for translation in translations:
            translation.master = self
            translation.save()
