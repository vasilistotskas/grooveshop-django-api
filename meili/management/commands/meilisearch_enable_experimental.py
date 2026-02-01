"""
Management command to enable experimental Meilisearch features.

This command enables experimental features like containsFilter via the
Meilisearch /experimental-features API endpoint.
"""

import requests
from django.core.management.base import BaseCommand
from django.utils.translation import gettext as _

from meili._client import client as meili_client


class Command(BaseCommand):
    help = _("Enable experimental Meilisearch features")

    AVAILABLE_FEATURES = {
        "containsFilter": "Enable CONTAINS operator for substring matching in filters",
        "vectorStore": "Enable vector search capabilities",
        "editDocumentsByFunction": "Enable document editing via functions",
    }

    def add_arguments(self, parser):
        parser.add_argument(
            "--feature",
            type=str,
            help=_(
                "Experimental feature to enable. Available: "
                + ", ".join(self.AVAILABLE_FEATURES.keys())
            ),
            required=True,
        )
        parser.add_argument(
            "--disable",
            action="store_true",
            help=_("Disable the feature instead of enabling it"),
        )

    def handle(self, *args, **options):
        feature = options["feature"]
        disable = options.get("disable", False)

        # Validate feature name
        if feature not in self.AVAILABLE_FEATURES:
            self.stdout.write(
                self.style.ERROR(
                    f"Unknown feature: {feature}\n\nAvailable features:\n"
                )
            )
            for feat_name, feat_desc in self.AVAILABLE_FEATURES.items():
                self.stdout.write(f"  - {feat_name}: {feat_desc}")
            return

        # Get Meilisearch connection details
        settings = meili_client.settings
        base_url = f"{'https' if settings.https else 'http'}://{settings.host}:{settings.port}"

        # Build API endpoint URL
        url = f"{base_url}/experimental-features"

        # Build headers
        headers = {}
        if settings.master_key:
            headers["Authorization"] = f"Bearer {settings.master_key}"
        headers["Content-Type"] = "application/json"

        # Build payload
        payload = {feature: not disable}

        # Display action
        action = "Disabling" if disable else "Enabling"
        self.stdout.write(f"\n{action} experimental feature: {feature}")
        self.stdout.write(f"Description: {self.AVAILABLE_FEATURES[feature]}\n")

        try:
            # Make API request
            response = requests.patch(
                url,
                json=payload,
                headers=headers,
                timeout=settings.timeout or 10,
            )

            # Check response
            if response.status_code == 200:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"✓ Successfully {action.lower()} {feature}"
                    )
                )

                # Display current experimental features status
                get_response = requests.get(
                    url,
                    headers=headers,
                    timeout=settings.timeout or 10,
                )

                if get_response.status_code == 200:
                    current_features = get_response.json()
                    self.stdout.write("\nCurrent experimental features status:")
                    for feat_name, enabled in current_features.items():
                        status_icon = "✓" if enabled else "✗"
                        status_text = "enabled" if enabled else "disabled"
                        self.stdout.write(
                            f"  {status_icon} {feat_name}: {status_text}"
                        )
            else:
                self.stdout.write(
                    self.style.ERROR(
                        f"✗ Failed to {action.lower()} {feature}\n"
                        f"Status code: {response.status_code}\n"
                        f"Response: {response.text}"
                    )
                )

        except requests.exceptions.ConnectionError:
            self.stdout.write(
                self.style.ERROR(
                    f"✗ Failed to connect to Meilisearch at {base_url}\n"
                    "Make sure Meilisearch is running."
                )
            )
        except requests.exceptions.Timeout:
            self.stdout.write(
                self.style.ERROR(
                    f"✗ Request timed out connecting to {base_url}"
                )
            )
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"✗ Unexpected error: {str(e)}"))
