#!/usr/bin/env python
"""Test script for configuration-driven email system."""

import os
import django

# Setup Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")
django.setup()

from core.email.preview_service import EmailTemplatePreviewService  # noqa: E402
from core.email.config import EmailTemplateConfig  # noqa: E402
from core.email.registry import EmailTemplateRegistry  # noqa: E402


def test_configuration():
    """Test configuration system."""
    print("Testing Configuration System...")
    print("=" * 60)

    # Test 1: Configuration loading
    print("\n1. Testing configuration loading:")
    config = EmailTemplateConfig.get_template_config("newsletter")
    if config:
        print(f"   OK - Newsletter config: {config.name}")
        print(f"        Category: {config.category_name}")
        print(f"        Subject: {config.subject_template}")
    else:
        print("   FAILED - Newsletter config not found")

    # Test 2: Category detection
    print("\n2. Testing category detection:")
    category = EmailTemplateConfig.get_category_for_template("newsletter")
    print(f"   Newsletter category: {category if category else 'root'}")

    category = EmailTemplateConfig.get_category_for_template(
        "order_confirmation"
    )
    print(f"   Order confirmation category: {category}")

    # Test 3: Context generator detection
    print("\n3. Testing context generator detection:")
    generator = EmailTemplateConfig.get_context_generator_for_template(
        "newsletter"
    )
    print(f"   Newsletter generator: {generator}")

    generator = EmailTemplateConfig.get_context_generator_for_template(
        "order_confirmation"
    )
    print(f"   Order generator: {generator}")

    # Test 4: Preview generation
    print("\n4. Testing preview generation:")
    service = EmailTemplatePreviewService()

    preview = service.generate_preview("newsletter")
    if preview.error:
        print(f"   FAILED - Newsletter: {preview.error}")
    else:
        print("   OK - Newsletter preview generated")
        print(f"        Subject: {preview.subject}")
        print(
            f"        Has user context: {'Hello John' in preview.html_content}"
        )

    preview = service.generate_preview("order_confirmation")
    if preview.error:
        print(f"   FAILED - Order: {preview.error}")
    else:
        print("   OK - Order preview generated")
        print(f"        Subject: {preview.subject}")
        print(
            f"        Has order context: {'order' in preview.html_content.lower()}"
        )

    # Test 5: Registry integration
    print("\n5. Testing registry integration:")
    registry = EmailTemplateRegistry()
    templates = registry.get_all_templates()
    print(f"   Total templates discovered: {len(templates)}")

    categories = registry.get_categories()
    print(f"   Categories: {', '.join(categories)}")

    print("\n" + "=" * 60)
    print("Configuration system test complete!")


if __name__ == "__main__":
    test_configuration()
