from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand
from parler.utils.context import switch_language

from loyalty.models.tier import LoyaltyTier

DEFAULT_TIERS = [
    {
        "required_level": 1,
        "points_multiplier": Decimal("1.0"),
        "sort_order": 1,
        "translations": {
            "en": {
                "name": "Bronze",
                "description": "Welcome tier for new members",
            },
            "el": {
                "name": "Χάλκινο",
                "description": "Αρχικό επίπεδο για νέα μέλη",
            },
            "de": {
                "name": "Bronze",
                "description": "Willkommensstufe für neue Mitglieder",
            },
        },
    },
    {
        "required_level": 5,
        "points_multiplier": Decimal("1.25"),
        "sort_order": 2,
        "translations": {
            "en": {
                "name": "Silver",
                "description": "Earn 25% bonus points on every purchase",
            },
            "el": {
                "name": "Ασημένιο",
                "description": "Κερδίστε 25% επιπλέον πόντους σε κάθε αγορά",
            },
            "de": {
                "name": "Silber",
                "description": "Verdienen Sie 25% Bonuspunkte bei jedem Einkauf",
            },
        },
    },
    {
        "required_level": 10,
        "points_multiplier": Decimal("1.5"),
        "sort_order": 3,
        "translations": {
            "en": {
                "name": "Gold",
                "description": "Earn 50% bonus points on every purchase",
            },
            "el": {
                "name": "Χρυσό",
                "description": "Κερδίστε 50% επιπλέον πόντους σε κάθε αγορά",
            },
            "de": {
                "name": "Gold",
                "description": "Verdienen Sie 50% Bonuspunkte bei jedem Einkauf",
            },
        },
    },
    {
        "required_level": 20,
        "points_multiplier": Decimal("2.0"),
        "sort_order": 4,
        "translations": {
            "en": {
                "name": "Platinum",
                "description": "Earn double points on every purchase",
            },
            "el": {
                "name": "Πλατινένιο",
                "description": "Κερδίστε διπλάσιους πόντους σε κάθε αγορά",
            },
            "de": {
                "name": "Platin",
                "description": "Verdienen Sie doppelte Punkte bei jedem Einkauf",
            },
        },
    },
    {
        "required_level": 50,
        "points_multiplier": Decimal("3.0"),
        "sort_order": 5,
        "translations": {
            "en": {
                "name": "Diamond",
                "description": "Earn triple points on every purchase",
            },
            "el": {
                "name": "Διαμάντι",
                "description": "Κερδίστε τριπλάσιους πόντους σε κάθε αγορά",
            },
            "de": {
                "name": "Diamant",
                "description": "Verdienen Sie dreifache Punkte bei jedem Einkauf",
            },
        },
    },
]


class Command(BaseCommand):
    help = "Seed default loyalty tiers with translations (el, en, de)"

    def handle(self, *args, **options):
        site_id = getattr(settings, "SITE_ID", 1)
        parler_languages = getattr(settings, "PARLER_LANGUAGES", {})

        languages = parler_languages.get(site_id, parler_languages.get(1, []))
        if not languages:
            language_codes = ["en", "el", "de"]
        else:
            language_codes = [lang["code"] for lang in languages]

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeding loyalty tiers for languages: {', '.join(language_codes)}"
            )
        )

        created_count = 0
        updated_count = 0

        for tier_data in DEFAULT_TIERS:
            tier, created = LoyaltyTier.objects.get_or_create(
                required_level=tier_data["required_level"],
                defaults={
                    "points_multiplier": tier_data["points_multiplier"],
                    "sort_order": tier_data["sort_order"],
                },
            )

            if created:
                created_count += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Created tier: level {tier_data['required_level']}"
                    )
                )
            else:
                tier.points_multiplier = tier_data["points_multiplier"]
                tier.sort_order = tier_data["sort_order"]
                tier.save()
                updated_count += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"Updated tier: level {tier_data['required_level']}"
                    )
                )

            translations = tier_data["translations"]
            for language_code in language_codes:
                if language_code in translations:
                    lang_data = translations[language_code]

                    with switch_language(tier, language_code):
                        tier.name = lang_data["name"]
                        tier.description = lang_data["description"]
                        tier.save()

                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  Added {language_code} translation for: {lang_data['name']}"
                        )
                    )
                else:
                    if "en" in translations:
                        lang_data = translations["en"]
                        with switch_language(tier, language_code):
                            tier.name = lang_data["name"]
                            tier.description = lang_data["description"]
                            tier.save()

                        self.stdout.write(
                            self.style.WARNING(
                                f"  Used English fallback for {language_code}: {lang_data['name']}"
                            )
                        )
                    else:
                        self.stdout.write(
                            self.style.WARNING(
                                f"  No translation available for {language_code}: level {tier_data['required_level']}"
                            )
                        )

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone: {created_count} tiers created, "
                f"{updated_count} tiers updated for {len(language_codes)} languages."
            )
        )
