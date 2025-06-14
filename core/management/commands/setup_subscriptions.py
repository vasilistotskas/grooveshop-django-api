from django.conf import settings
from django.core.management.base import BaseCommand
from parler.utils.context import switch_language

from user.models.subscription import SubscriptionTopic


class Command(BaseCommand):
    help = "Set up default subscription topics for all configured languages"

    def handle(self, *args, **options):
        site_id = getattr(settings, "SITE_ID", 1)
        parler_languages = getattr(settings, "PARLER_LANGUAGES", {})

        languages = parler_languages.get(site_id, parler_languages.get(1, []))
        if not languages:
            self.stdout.write(
                self.style.ERROR(
                    "No languages found in PARLER_LANGUAGES setting"
                )
            )
            return

        language_codes = [lang["code"] for lang in languages]

        self.stdout.write(
            self.style.SUCCESS(
                f"Creating topics for languages: {', '.join(language_codes)}"
            )
        )

        topics_translations = {
            "en": [
                {
                    "slug": "weekly-newsletter",
                    "name": "Weekly Newsletter",
                    "description": "Get our weekly roundup of news, updates, and featured content",
                    "category": SubscriptionTopic.TopicCategory.NEWSLETTER,
                    "is_default": True,
                },
                {
                    "slug": "product-updates",
                    "name": "Product Updates",
                    "description": "Be the first to know about new features and improvements",
                    "category": SubscriptionTopic.TopicCategory.PRODUCT,
                    "is_default": False,
                },
                {
                    "slug": "promotional-offers",
                    "name": "Promotional Offers",
                    "description": "Exclusive deals and special offers for subscribers",
                    "category": SubscriptionTopic.TopicCategory.MARKETING,
                    "is_default": False,
                },
                {
                    "slug": "system-notifications",
                    "name": "System Notifications",
                    "description": "Important system updates and maintenance notices",
                    "category": SubscriptionTopic.TopicCategory.SYSTEM,
                    "is_default": True,
                    "requires_confirmation": False,
                },
                {
                    "slug": "monthly-digest",
                    "name": "Monthly Digest",
                    "description": "Monthly summary of activity and highlights",
                    "category": SubscriptionTopic.TopicCategory.NEWSLETTER,
                    "is_default": False,
                },
                {
                    "slug": "new-features",
                    "name": "New Feature Announcements",
                    "description": "Get notified when we launch new features",
                    "category": SubscriptionTopic.TopicCategory.PRODUCT,
                    "is_default": False,
                },
                {
                    "slug": "tips-and-tricks",
                    "name": "Tips & Tricks",
                    "description": "Learn how to get the most out of our platform",
                    "category": SubscriptionTopic.TopicCategory.OTHER,
                    "is_default": False,
                },
            ],
            "el": [
                {
                    "slug": "weekly-newsletter",
                    "name": "Εβδομαδιαίο Newsletter",
                    "description": "Λάβετε την εβδομαδιαία συλλογή νέων, ενημερώσεων και περιεχομένου",
                    "category": SubscriptionTopic.TopicCategory.NEWSLETTER,
                    "is_default": True,
                },
                {
                    "slug": "product-updates",
                    "name": "Ενημερώσεις Προϊόντων",
                    "description": "Μάθετε πρώτοι για νέες λειτουργίες και βελτιώσεις",  # noqa: RUF001
                    "category": SubscriptionTopic.TopicCategory.PRODUCT,
                    "is_default": False,
                },
                {
                    "slug": "promotional-offers",
                    "name": "Προωθητικές Προσφορές",
                    "description": "Αποκλειστικές προσφορές για συνδρομητές",  # noqa: RUF001
                    "category": SubscriptionTopic.TopicCategory.MARKETING,
                    "is_default": False,
                },
                {
                    "slug": "system-notifications",
                    "name": "Ειδοποιήσεις Συστήματος",
                    "description": "Σημαντικές ενημερώσεις συστήματος και ειδοποιήσεις συντήρησης",
                    "category": SubscriptionTopic.TopicCategory.SYSTEM,
                    "is_default": True,
                    "requires_confirmation": False,
                },
                {
                    "slug": "monthly-digest",
                    "name": "Μηνιαία Περίληψη",
                    "description": "Μηνιαία περίληψη δραστηριότητας και σημαντικών στιγμών",
                    "category": SubscriptionTopic.TopicCategory.NEWSLETTER,
                    "is_default": False,
                },
                {
                    "slug": "new-features",
                    "name": "Ανακοινώσεις Νέων Λειτουργιών",
                    "description": "Ενημερωθείτε όταν κυκλοφορούν νέες λειτουργίες",
                    "category": SubscriptionTopic.TopicCategory.PRODUCT,
                    "is_default": False,
                },
                {
                    "slug": "tips-and-tricks",
                    "name": "Συμβουλές & Κόλπα",
                    "description": "Μάθετε πώς να αξιοποιήσετε στο έπακρο την πλατφόρμα μας",  # noqa: RUF001
                    "category": SubscriptionTopic.TopicCategory.OTHER,
                    "is_default": False,
                },
            ],
            "de": [
                {
                    "slug": "weekly-newsletter",
                    "name": "Wöchentlicher Newsletter",
                    "description": "Erhalten Sie unsere wöchentliche Zusammenfassung von Nachrichten, Updates und Inhalten",
                    "category": SubscriptionTopic.TopicCategory.NEWSLETTER,
                    "is_default": True,
                },
                {
                    "slug": "product-updates",
                    "name": "Produkt-Updates",
                    "description": "Erfahren Sie als Erste von neuen Funktionen und Verbesserungen",
                    "category": SubscriptionTopic.TopicCategory.PRODUCT,
                    "is_default": False,
                },
                {
                    "slug": "promotional-offers",
                    "name": "Werbeaktionen",
                    "description": "Exklusive Angebote und Sonderaktionen für Abonnenten",
                    "category": SubscriptionTopic.TopicCategory.MARKETING,
                    "is_default": False,
                },
                {
                    "slug": "system-notifications",
                    "name": "System-Benachrichtigungen",
                    "description": "Wichtige System-Updates und Wartungshinweise",
                    "category": SubscriptionTopic.TopicCategory.SYSTEM,
                    "is_default": True,
                    "requires_confirmation": False,
                },
                {
                    "slug": "monthly-digest",
                    "name": "Monatliche Zusammenfassung",
                    "description": "Monatliche Zusammenfassung von Aktivitäten und Highlights",
                    "category": SubscriptionTopic.TopicCategory.NEWSLETTER,
                    "is_default": False,
                },
                {
                    "slug": "new-features",
                    "name": "Neue Feature-Ankündigungen",
                    "description": "Benachrichtigung bei neuen Funktionen",
                    "category": SubscriptionTopic.TopicCategory.PRODUCT,
                    "is_default": False,
                },
                {
                    "slug": "tips-and-tricks",
                    "name": "Tipps & Tricks",
                    "description": "Lernen Sie, wie Sie das Beste aus unserer Plattform herausholen",
                    "category": SubscriptionTopic.TopicCategory.OTHER,
                    "is_default": False,
                },
            ],
        }

        created_count = 0
        updated_count = 0

        default_topics = topics_translations.get("en", [])

        for topic_data in default_topics:
            slug = topic_data["slug"]

            topic, created = SubscriptionTopic.objects.get_or_create(
                slug=slug,
                defaults={
                    "category": topic_data["category"],
                    "is_default": topic_data.get("is_default", False),
                    "requires_confirmation": topic_data.get(
                        "requires_confirmation", True
                    ),
                },
            )

            if created:
                created_count += 1
                self.stdout.write(self.style.SUCCESS(f"Created topic: {slug}"))
            else:
                updated_count += 1
                self.stdout.write(self.style.WARNING(f"Updated topic: {slug}"))

            for language_code in language_codes:
                if language_code in topics_translations:
                    lang_topics = topics_translations[language_code]
                    lang_topic_data = next(
                        (t for t in lang_topics if t["slug"] == slug), None
                    )

                    if lang_topic_data:
                        with switch_language(topic, language_code):
                            topic.name = lang_topic_data["name"]
                            topic.description = lang_topic_data["description"]
                            topic.save()

                        self.stdout.write(
                            self.style.SUCCESS(
                                f"  Added {language_code} translation for: {lang_topic_data['name']}"
                            )
                        )
                    else:
                        self.stdout.write(
                            self.style.WARNING(
                                f"  No {language_code} translation found for: {slug}"
                            )
                        )
                else:
                    self.stdout.write(
                        self.style.WARNING(
                            f"  No translations available for language: {language_code}"
                        )
                    )

        self.stdout.write(
            self.style.SUCCESS(
                f"\nSummary: {created_count} topics created, "
                f"{updated_count} topics updated for {len(language_codes)} languages."
            )
        )
