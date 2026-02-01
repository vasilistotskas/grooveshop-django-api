"""
Management command to update Meilisearch ranking rules.

This command allows updating ranking rules for specific indexes to customize
result ordering based on business metrics.
"""

from django.core.management.base import BaseCommand
from django.utils.translation import gettext as _

from blog.models.post import BlogPostTranslation
from meili._client import client as meili_client
from meili.dataclasses import MeiliIndexSettings
from product.models.product import ProductTranslation


class Command(BaseCommand):
    help = _("Update Meilisearch ranking rules for specific index")

    AVAILABLE_INDEXES = {
        "ProductTranslation": ProductTranslation,
        "BlogPostTranslation": BlogPostTranslation,
    }

    VALID_RANKING_RULES = [
        "words",
        "typo",
        "proximity",
        "attribute",
        "sort",
        "exactness",
    ]

    def add_arguments(self, parser):
        parser.add_argument(
            "--index",
            type=str,
            help=_(
                "Index to update. Available: "
                + ", ".join(self.AVAILABLE_INDEXES.keys())
            ),
            required=True,
        )
        parser.add_argument(
            "--rules",
            type=str,
            help=_(
                "Comma-separated ranking rules. "
                "Example: words,typo,proximity,attribute,sort,stock:desc,exactness"
            ),
            required=True,
        )

    def handle(self, *args, **options):
        index_name = options["index"]
        rules_str = options["rules"]

        # Validate index name
        if index_name not in self.AVAILABLE_INDEXES:
            self.stdout.write(
                self.style.ERROR(
                    f"Unknown index: {index_name}\n\nAvailable indexes:\n"
                )
            )
            for idx_name in self.AVAILABLE_INDEXES.keys():
                self.stdout.write(f"  - {idx_name}")
            return

        # Parse ranking rules
        rules = [rule.strip() for rule in rules_str.split(",") if rule.strip()]

        if not rules:
            self.stdout.write(
                self.style.ERROR(
                    "No ranking rules provided. "
                    "Please provide comma-separated rules."
                )
            )
            return

        # Validate ranking rules
        validation_errors = self._validate_ranking_rules(rules)
        if validation_errors:
            self.stdout.write(self.style.ERROR("Invalid ranking rules:\n"))
            for error in validation_errors:
                self.stdout.write(f"  - {error}")
            self.stdout.write(
                "\nValid base rules: " + ", ".join(self.VALID_RANKING_RULES)
            )
            self.stdout.write("\nCustom rules format: field:asc or field:desc")
            return

        # Get model class
        model_class = self.AVAILABLE_INDEXES[index_name]

        # Display action
        self.stdout.write(f"\nUpdating {index_name} ranking rules...")
        self.stdout.write("\nNew ranking rules:")
        for i, rule in enumerate(rules, 1):
            self.stdout.write(f"  {i}. {rule}")

        try:
            # Create settings object with ranking rules
            index_settings = MeiliIndexSettings(ranking_rules=rules)

            # Get index name from model
            meili_index_name = model_class._meilisearch["index_name"]

            # Apply settings
            meili_client.with_settings(meili_index_name, index_settings)

            self.stdout.write(
                self.style.SUCCESS(
                    f"\n✓ Successfully updated {index_name} ranking rules"
                )
            )
            self.stdout.write(
                "\nNote: Ranking rules are applied immediately to subsequent searches."
            )

        except Exception as e:
            self.stdout.write(
                self.style.ERROR(
                    f"✗ Failed to update {index_name} ranking rules: {str(e)}"
                )
            )

    def _validate_ranking_rules(self, rules):
        """
        Validate ranking rules syntax.

        Returns list of validation errors, empty if all rules are valid.
        """
        errors = []

        for rule in rules:
            # Check if it's a base rule
            if rule in self.VALID_RANKING_RULES:
                continue

            # Check if it's a custom rule (field:asc or field:desc)
            if ":" in rule:
                parts = rule.split(":")
                if len(parts) != 2:
                    errors.append(
                        f"Invalid custom rule format: {rule}. "
                        "Expected format: field:asc or field:desc"
                    )
                    continue

                field, direction = parts
                if direction not in ["asc", "desc"]:
                    errors.append(
                        f"Invalid sort direction in rule: {rule}. "
                        "Must be 'asc' or 'desc'"
                    )
            else:
                errors.append(
                    f"Unknown ranking rule: {rule}. "
                    f"Valid base rules: {', '.join(self.VALID_RANKING_RULES)}"
                )

        return errors
