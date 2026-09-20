"""The demo store's offers: what is on, in both languages.

Kept apart from ``demo_store`` for the same reason as the catalogue —
this is DATA. The seeder decides how a row is written; this decides what
it says.

Twelve rows, chosen to cover the whole surface rather than to look busy:
every ``BenefitType``, both triggers, a bulk code, and one expired row
that exists only so the admin's list has something in the past. The demo
store's promotions used to be created by hand through kubectl and were
never written down, so they drifted — by 2026-09 several pointed at
products that had since been deactivated, which is invisible on
``/offers`` (the card renders, the cart refuses) and impossible to
reproduce after a reseed.

Two rules run through it:

* **Every row carries Greek AND English.** ``Promotion`` resolves its
  name and description SERVER-side, so an untranslated row is Greek on
  ``/en`` with nothing to fall back to.
* **Every product and category named here is one the catalogue seeds.**
  ``demo_catalogue`` is the only source of slugs; the seeder fails loudly
  on a slug it cannot resolve rather than quietly writing an offer that
  targets nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from promotion.enum import BenefitType, PromotionTrigger, TargetScope


@dataclass(frozen=True)
class PromotionRow:
    #: Greek name, and the natural key: promotions have no slug, so this
    #: is what a re-run matches on (the same convention Attribute and
    #: Tag use).
    name_el: str
    name_en: str
    description_el: str
    description_en: str
    trigger: str
    benefit_type: str
    target_scope: str
    benefit_value: str = "0"
    #: Catalogue slugs. `products`/`categories` are the targets,
    #: `get_products` the reward pool for BXGY and FREE_GIFT.
    products: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    excluded_products: tuple[str, ...] = ()
    excluded_categories: tuple[str, ...] = ()
    get_products: tuple[str, ...] = ()
    exclude_discounted_products: bool = False
    min_quantity: int | None = None
    buy_quantity: int | None = None
    get_quantity: int | None = None
    get_discount_percent: str = "100"
    #: EUR, as a string so the dataset never carries a float.
    min_subtotal: str | None = None
    max_discount_amount: str | None = None
    first_order_only: bool = False
    stackable: bool = False
    priority: int = 0
    usage_limit_total: int | None = None
    usage_limit_per_customer: int | None = None
    #: Days from "now". Negative is in the past — which is the point of
    #: the expired row.
    starts_in_days: int | None = None
    ends_in_days: int | None = None
    is_active: bool = True
    #: A CODE promotion needs at least one; AUTOMATIC rows have none.
    codes: tuple[str, ...] = ()
    #: Per-code usage cap, for bulk codes. Applies to every code in
    #: `codes`.
    code_usage_limit: int | None = None
    #: Excluded from the public listing assertions: a row that is
    #: deliberately not listable (expired, or single-use bulk codes).
    publicly_listed: bool = True


PROMOTIONS: tuple[PromotionRow, ...] = (
    PromotionRow(
        name_el="Καλωσόρισμα -10%",
        name_en="Welcome 10% off",
        description_el="Έκπτωση 10% στην πρώτη σου παραγγελία, έως 15€.",
        description_en="10% off your first order, up to 15€.",
        trigger=PromotionTrigger.CODE,
        benefit_type=BenefitType.PERCENTAGE,
        target_scope=TargetScope.ORDER,
        benefit_value="10",
        max_discount_amount="15.00",
        first_order_only=True,
        stackable=True,
        priority=10,
        codes=("WELCOME10",),
    ),
    PromotionRow(
        name_el="5€ έκπτωση από 25€",
        name_en="5€ off over 25€",
        description_el="Έκπτωση 5€ σε παραγγελίες από 25€ και πάνω.",
        description_en="5€ off any order of 25€ or more.",
        trigger=PromotionTrigger.CODE,
        benefit_type=BenefitType.FIXED_AMOUNT,
        target_scope=TargetScope.ORDER,
        benefit_value="5.00",
        min_subtotal="25.00",
        stackable=True,
        priority=20,
        codes=("SAVE5",),
    ),
    PromotionRow(
        name_el="-20% στους φορτιστές GaN",
        name_en="20% off GaN chargers",
        description_el=(
            "Έκπτωση 20% σε όλους τους φορτιστές πρίζας, εκτός από όσους "
            "έχουν ήδη έκπτωση."
        ),
        description_en=(
            "20% off every wall charger, except the ones already marked down."
        ),
        trigger=PromotionTrigger.CODE,
        benefit_type=BenefitType.PERCENTAGE,
        target_scope=TargetScope.CATEGORIES,
        benefit_value="20",
        categories=("demo-wall-chargers",),
        exclude_discounted_products=True,
        stackable=False,
        priority=30,
        codes=("GAN20",),
    ),
    PromotionRow(
        name_el="8€ έκπτωση σε power bank",
        name_en="8€ off a power bank",
        description_el=(
            "Έκπτωση 8€ σε τρία επιλεγμένα power bank. Μία χρήση ανά πελάτη."
        ),
        description_en=(
            "8€ off three selected power banks. One use per customer."
        ),
        trigger=PromotionTrigger.CODE,
        benefit_type=BenefitType.FIXED_AMOUNT,
        target_scope=TargetScope.PRODUCTS,
        benefit_value="8.00",
        products=(
            "demo-powerbank-20k",
            "demo-powerbank-magnetic",
            "demo-powerbank-red",
        ),
        usage_limit_per_customer=1,
        stackable=False,
        priority=40,
        codes=("POWER8",),
    ),
    PromotionRow(
        name_el="Δωρεάν αποστολή από 30€",
        name_en="Free delivery over 30€",
        description_el="Δωρεάν αποστολή σε παραγγελίες από 30€ και πάνω.",
        description_en="Free delivery on any order of 30€ or more.",
        trigger=PromotionTrigger.CODE,
        benefit_type=BenefitType.FREE_SHIPPING,
        target_scope=TargetScope.ORDER,
        min_subtotal="30.00",
        stackable=True,
        priority=50,
        codes=("SHIPFREE",),
    ),
    PromotionRow(
        name_el="2+1 δώρο σε καλώδια",
        name_en="Buy 2 cables, get 1 free",
        description_el=(
            "Αγοράζεις δύο καλώδια USB-C, το τρίτο είναι δώρο. Μπαίνει "
            "αυτόματα στο καλάθι."
        ),
        description_en=(
            "Buy two USB-C cables and the third is free. Applied "
            "automatically in the cart."
        ),
        trigger=PromotionTrigger.AUTOMATIC,
        benefit_type=BenefitType.BXGY,
        target_scope=TargetScope.CATEGORIES,
        categories=("demo-usb-c-cables",),
        buy_quantity=2,
        get_quantity=1,
        get_discount_percent="100",
        stackable=False,
        priority=60,
    ),
    PromotionRow(
        name_el="Θήκη + τζαμάκι -50%",
        name_en="Case + screen protector 50% off",
        description_el=("Με μία θήκη, το τζαμάκι προστασίας στη μισή τιμή."),
        description_en=("Buy a case and the screen protector is half price."),
        trigger=PromotionTrigger.AUTOMATIC,
        benefit_type=BenefitType.BXGY,
        target_scope=TargetScope.CATEGORIES,
        categories=("demo-cases",),
        get_products=(
            "demo-glass-2pack",
            "demo-glass-privacy",
            "demo-glass-camera",
        ),
        buy_quantity=1,
        get_quantity=1,
        get_discount_percent="50",
        stackable=True,
        priority=70,
    ),
    PromotionRow(
        name_el="Δώρο καλώδιο 20 cm από 60€",
        name_en="Free 20 cm cable over 60€",
        description_el=(
            "Σε κάθε παραγγελία από 60€ και πάνω, ένα καλώδιο USB-C 20 cm δώρο."
        ),
        description_en=(
            "Every order of 60€ or more comes with a free 20 cm USB-C cable."
        ),
        trigger=PromotionTrigger.AUTOMATIC,
        benefit_type=BenefitType.FREE_GIFT,
        target_scope=TargetScope.ORDER,
        get_products=("demo-cable-usbc-20cm",),
        get_quantity=1,
        min_subtotal="60.00",
        stackable=True,
        priority=80,
    ),
    PromotionRow(
        name_el="-15% στον ήχο",
        name_en="15% off audio",
        description_el="Έκπτωση 15% σε ακουστικά και ηχεία, για λίγες μέρες.",
        description_en="15% off earbuds and speakers, for a few days only.",
        trigger=PromotionTrigger.AUTOMATIC,
        benefit_type=BenefitType.PERCENTAGE,
        target_scope=TargetScope.CATEGORIES,
        benefit_value="15",
        categories=("demo-audio",),
        exclude_discounted_products=True,
        ends_in_days=30,
        stackable=True,
        priority=90,
    ),
    PromotionRow(
        name_el="-5% από 3 τεμάχια",
        name_en="5% off from 3 items",
        description_el=(
            "Έκπτωση 5% σε όλο το καλάθι όταν έχει τρία τεμάχια ή περισσότερα."
        ),
        description_en=(
            "5% off the whole cart once it holds three items or more."
        ),
        trigger=PromotionTrigger.AUTOMATIC,
        benefit_type=BenefitType.PERCENTAGE,
        target_scope=TargetScope.ORDER,
        benefit_value="5",
        min_quantity=3,
        stackable=True,
        priority=100,
    ),
    PromotionRow(
        name_el="VIP -25%",
        name_en="VIP 25% off",
        description_el=(
            "Προσωπικός κωδικός -25%. Κάθε κωδικός ισχύει για μία παραγγελία."
        ),
        description_en=(
            "A personal 25% code. Each code is good for one order."
        ),
        trigger=PromotionTrigger.CODE,
        benefit_type=BenefitType.PERCENTAGE,
        target_scope=TargetScope.ORDER,
        benefit_value="25",
        usage_limit_total=50,
        stackable=False,
        priority=110,
        codes=("VIP25A", "VIP25B", "VIP25C", "VIP25D", "VIP25E"),
        code_usage_limit=1,
        # Single-use codes are never advertised: `publicly_listable`
        # excludes a code whose `usage_limit` is 1, because telling
        # every shopper about a coupon only one of them can use is a
        # promise the cart then breaks.
        publicly_listed=False,
    ),
    PromotionRow(
        name_el="Παλιά προσφορά -3€",
        name_en="Old offer 3€ off",
        description_el="Προσφορά που έχει λήξει — υπάρχει για το ιστορικό.",
        description_en="An offer that has ended — kept for the record.",
        trigger=PromotionTrigger.CODE,
        benefit_type=BenefitType.FIXED_AMOUNT,
        target_scope=TargetScope.ORDER,
        benefit_value="3.00",
        starts_in_days=-60,
        ends_in_days=-30,
        stackable=True,
        priority=120,
        codes=("OLD3",),
        publicly_listed=False,
    ),
)


def _bump(report: dict[str, int], key: str, amount: int = 1) -> None:
    report[key] = report.get(key, 0) + amount


def seed_promotions(translate) -> dict[str, int]:
    """Twelve offers, their coupon codes and their targets.

    ``translate`` is passed in rather than imported so this module stays
    a dataset plus one function, and the caller keeps owning how a
    translation is written.

    Matched on the GREEK name, because a promotion has no slug. Rows are
    rewritten in full on every run — the demo store has no operator, so
    the dataset is the only author these rows have, and a re-run is how
    a corrected price cap or a fixed translation reaches staging.

    Every slug is resolved against what the catalogue actually seeded
    and a miss RAISES. An offer whose targets silently resolved to
    nothing is the exact failure this file exists to end: the card still
    renders on ``/offers`` and the cart then refuses the code.
    """
    from django.utils import timezone
    from djmoney.money import Money

    from product.models.category import ProductCategory
    from product.models.product import Product
    from promotion.models.code import PromotionCode
    from promotion.models.promotion import Promotion

    report: dict[str, int] = {}

    def products_for(slugs: tuple[str, ...], where: str) -> list[Product]:
        if not slugs:
            return []
        found = {p.slug: p for p in Product.objects.filter(slug__in=slugs)}
        missing = sorted(set(slugs) - set(found))
        if missing:
            raise ValueError(f"{where}: no such product slug(s): {missing}")
        return [found[slug] for slug in slugs]

    def categories_for(
        slugs: tuple[str, ...], where: str
    ) -> list[ProductCategory]:
        if not slugs:
            return []
        found = {
            c.slug: c for c in ProductCategory.objects.filter(slug__in=slugs)
        }
        missing = sorted(set(slugs) - set(found))
        if missing:
            raise ValueError(f"{where}: no such category slug(s): {missing}")
        return [found[slug] for slug in slugs]

    def money(value: str | None) -> Money | None:
        return None if value is None else Money(Decimal(value), "EUR")

    def when(days: int | None):
        return None if days is None else timezone.now() + timedelta(days=days)

    for row in PROMOTIONS:
        promotion = Promotion.objects.filter(
            translations__language_code="el",
            translations__name=row.name_el,
        ).first()
        created = promotion is None
        if created:
            promotion = Promotion()

        promotion.trigger = row.trigger
        promotion.benefit_type = row.benefit_type
        promotion.benefit_value = Decimal(row.benefit_value)
        promotion.target_scope = row.target_scope
        promotion.exclude_discounted_products = row.exclude_discounted_products
        promotion.min_quantity = row.min_quantity
        promotion.buy_quantity = row.buy_quantity
        promotion.get_quantity = row.get_quantity
        promotion.get_discount_percent = Decimal(row.get_discount_percent)
        promotion.min_subtotal = money(row.min_subtotal)
        promotion.max_discount_amount = money(row.max_discount_amount)
        promotion.first_order_only = row.first_order_only
        promotion.is_active = row.is_active
        promotion.starts_at = when(row.starts_in_days)
        promotion.ends_at = when(row.ends_in_days)
        promotion.stackable = row.stackable
        promotion.priority = row.priority
        promotion.usage_limit_total = row.usage_limit_total
        promotion.usage_limit_per_customer = row.usage_limit_per_customer

        translate(
            promotion, "el", name=row.name_el, description=row.description_el
        )
        translate(
            promotion, "en", name=row.name_en, description=row.description_en
        )

        # ``clean()`` carries every cross-field rule the admin enforces
        # (a percentage inside 0-100, BXGY with both quantities, an end
        # after its start). ``save()`` never calls it, so a bare ORM
        # write would store a row the engine then refuses to apply.
        promotion.clean()
        promotion.save()

        promotion.products.set(products_for(row.products, row.name_el))
        promotion.categories.set(categories_for(row.categories, row.name_el))
        promotion.excluded_products.set(
            products_for(row.excluded_products, row.name_el)
        )
        promotion.excluded_categories.set(
            categories_for(row.excluded_categories, row.name_el)
        )
        promotion.get_products.set(products_for(row.get_products, row.name_el))

        _bump(report, "created" if created else "updated")

        for code in row.codes:
            _, code_created = PromotionCode.objects.update_or_create(
                code=code,
                defaults={
                    "promotion": promotion,
                    "usage_limit": row.code_usage_limit,
                    "is_active": True,
                },
            )
            _bump(report, "codes_created" if code_created else "codes_updated")

    return report
