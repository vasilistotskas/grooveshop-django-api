from django.conf import settings
from django.core.management.base import BaseCommand
from djmoney.money import Money
from parler.utils.context import switch_language

from pay_way.models import PayWay


class Command(BaseCommand):
    help = "Set up payment methods (PayWays) for all configured languages and providers"

    def add_arguments(self, parser):
        parser.add_argument(
            "--provider",
            type=str,
            help="Specific provider to create (stripe, paypal, etc.). If not specified, creates all providers.",
        )

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
        default_currency = getattr(settings, "DEFAULT_CURRENCY", "EUR")

        self.stdout.write(
            self.style.SUCCESS(
                f"Creating PayWays for languages: {', '.join(language_codes)}"
            )
        )

        providers_config = {
            "stripe": {
                "provider_code": "stripe",
                "cost": Money(0, default_currency),
                "free_threshold": Money(0, default_currency),
                "is_online_payment": True,
                "requires_confirmation": False,
                "active": True,
                "sort_order": 1,
                "configuration": {
                    "accepted_payment_methods": [
                        "*",
                    ],
                    "capture_method": "automatic",
                    "setup_future_usage": None,
                    "payment_method_options": {
                        "card": {"request_three_d_secure": "automatic"}
                    },
                },
                "translations": {
                    "en": {
                        "name": "Credit Card",
                        "description": "Pay securely with your credit or debit card",
                        "instructions": "Your payment will be processed securely by Stripe",
                    },
                    "el": {
                        "name": "Πιστωτική Κάρτα",
                        "description": "Πληρώστε με ασφάλεια με την πιστωτική ή χρεωστική σας κάρτα",
                        "instructions": "Η πληρωμή σας θα επεξεργαστεί με ασφάλεια από τη Stripe",
                    },
                    "de": {
                        "name": "Kreditkarte",
                        "description": "Bezahlen Sie sicher mit Ihrer Kredit- oder Debitkarte",
                        "instructions": "Ihre Zahlung wird sicher von Stripe verarbeitet",
                    },
                },
            },
            "paypal": {
                "provider_code": "paypal",
                "cost": Money(0, default_currency),
                "free_threshold": Money(0, default_currency),
                "is_online_payment": True,
                "requires_confirmation": False,
                "active": True,
                "sort_order": 2,
                "configuration": {
                    "environment": "sandbox",
                    "accepted_funding": ["paypal", "card", "credit"],
                    "disable_funding": ["paylater"],
                    "style": {
                        "layout": "vertical",
                        "color": "blue",
                        "shape": "rect",
                        "label": "paypal",
                    },
                },
                "translations": {
                    "en": {
                        "name": "PayPal",
                        "description": "Pay with your PayPal account or credit card",
                        "instructions": "You will be redirected to PayPal to complete your payment",
                    },
                    "el": {
                        "name": "PayPal",
                        "description": "Πληρώστε με τον λογαριασμό PayPal ή την πιστωτική σας κάρτα",
                        "instructions": "Θα ανακατευθυνθείτε στο PayPal για να ολοκληρώσετε την πληρωμή",
                    },
                    "de": {
                        "name": "PayPal",
                        "description": "Bezahlen Sie mit Ihrem PayPal-Konto oder Ihrer Kreditkarte",
                        "instructions": "Sie werden zu PayPal weitergeleitet, um Ihre Zahlung abzuschließen",
                    },
                },
            },
            "bank_transfer": {
                "provider_code": "bank_transfer",
                "cost": Money(0, default_currency),
                "free_threshold": Money(50, default_currency),
                "is_online_payment": False,
                "requires_confirmation": True,
                "active": True,
                "sort_order": 3,
                "configuration": {
                    "bank_details": {
                        "account_holder": "Your Company Name",
                        "iban": "DE89 3704 0044 0532 0130 00",
                        "bic": "COBADEFFXXX",
                        "bank_name": "Commerzbank AG",
                    },
                    "reference_format": "ORDER-{order_id}",
                    "confirmation_required": True,
                },
                "translations": {
                    "en": {
                        "name": "Bank Transfer",
                        "description": "Pay by bank transfer (SEPA)",
                        "instructions": "Please transfer the total amount to our bank account using the provided reference number. Your order will be processed after payment confirmation.",
                    },
                    "el": {
                        "name": "Τραπεζικό Έμβασμα",
                        "description": "Πληρωμή με τραπεζικό έμβασμα (SEPA)",
                        "instructions": "Παρακαλώ μεταφέρετε το συνολικό ποσό στον τραπεζικό μας λογαριασμό χρησιμοποιώντας τον αριθμό αναφοράς. Η παραγγελία σας θα επεξεργαστεί μετά την επιβεβαίωση της πληρωμής.",
                    },
                    "de": {
                        "name": "Banküberweisung",
                        "description": "Zahlung per Banküberweisung (SEPA)",
                        "instructions": "Bitte überweisen Sie den Gesamtbetrag unter Angabe der Referenznummer auf unser Bankkonto. Ihre Bestellung wird nach Zahlungsbestätigung bearbeitet.",
                    },
                },
            },
            "cash_on_delivery": {
                "provider_code": "cash_on_delivery",
                "cost": Money(3.50, default_currency),
                "free_threshold": Money(100, default_currency),
                "is_online_payment": False,
                "requires_confirmation": False,
                "active": True,
                "sort_order": 4,
                "configuration": {
                    "max_order_value": 500,
                    "available_regions": ["domestic"],
                    "cash_handling_fee": 3.50,
                },
                "translations": {
                    "en": {
                        "name": "Cash on Delivery",
                        "description": "Pay in cash when your order is delivered",
                        "instructions": "You can pay in cash to the delivery person when your order arrives. A small handling fee applies.",
                    },
                    "el": {
                        "name": "Αντικαταβολή",
                        "description": "Πληρωμή σε μετρητά κατά την παράδοση",
                        "instructions": "Μπορείτε να πληρώσετε σε μετρητά στον διανομέα όταν φτάσει η παραγγελία σας. Ισχύει μικρή χρέωση διαχείρισης.",
                    },
                    "de": {
                        "name": "Nachnahme",
                        "description": "Barzahlung bei Lieferung",
                        "instructions": "Sie können bei der Ankunft Ihrer Bestellung bar an den Zusteller zahlen. Es fällt eine kleine Bearbeitungsgebühr an.",
                    },
                },
            },
        }

        target_provider = options.get("provider")
        if target_provider:
            if target_provider not in providers_config:
                self.stdout.write(
                    self.style.ERROR(
                        f"Unknown provider: {target_provider}. Available providers: {', '.join(providers_config.keys())}"
                    )
                )
                return
            providers_config = {
                target_provider: providers_config[target_provider]
            }

        created_count = 0
        updated_count = 0

        for provider_key, provider_data in providers_config.items():
            provider_code = provider_data["provider_code"]

            payway, created = PayWay.objects.get_or_create(
                provider_code=provider_code,
                defaults={
                    "cost": provider_data["cost"],
                    "free_threshold": provider_data["free_threshold"],
                    "is_online_payment": provider_data["is_online_payment"],
                    "requires_confirmation": provider_data[
                        "requires_confirmation"
                    ],
                    "active": provider_data["active"],
                    "sort_order": provider_data["sort_order"],
                    "configuration": provider_data["configuration"],
                },
            )

            if created:
                created_count += 1
                self.stdout.write(
                    self.style.SUCCESS(f"Created PayWay: {provider_code}")
                )
            else:
                payway.configuration = provider_data["configuration"]
                payway.cost = provider_data["cost"]
                payway.free_threshold = provider_data["free_threshold"]
                payway.save()
                updated_count += 1
                self.stdout.write(
                    self.style.WARNING(f"Updated PayWay: {provider_code}")
                )

            translations = provider_data["translations"]
            for language_code in language_codes:
                if language_code in translations:
                    lang_data = translations[language_code]

                    with switch_language(payway, language_code):
                        payway.name = lang_data["name"]
                        payway.description = lang_data["description"]
                        payway.instructions = lang_data["instructions"]
                        payway.save()

                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  Added {language_code} translation for: {lang_data['name']}"
                        )
                    )
                else:
                    if "en" in translations:
                        lang_data = translations["en"]
                        with switch_language(payway, language_code):
                            payway.name = lang_data["name"]
                            payway.description = lang_data["description"]
                            payway.instructions = lang_data["instructions"]
                            payway.save()

                        self.stdout.write(
                            self.style.WARNING(
                                f"  Used English fallback for {language_code}: {lang_data['name']}"
                            )
                        )
                    else:
                        self.stdout.write(
                            self.style.WARNING(
                                f"  No translation available for {language_code}: {provider_code}"
                            )
                        )

        self.stdout.write(
            self.style.SUCCESS(
                f"\nSummary: {created_count} PayWays created, "
                f"{updated_count} PayWays updated for {len(language_codes)} languages."
            )
        )

        if target_provider:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Provider '{target_provider}' setup completed."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS("All payment providers setup completed.")
            )
