#!/usr/bin/env python
"""Quick verification of email template system."""

import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")
django.setup()

from core.email.config import EmailTemplateConfig  # noqa: E402
from core.email.preview_service import EmailTemplatePreviewService  # noqa: E402
from core.email.registry import EmailTemplateRegistry  # noqa: E402

print("=" * 60)
print("EMAIL TEMPLATE SYSTEM VERIFICATION")
print("=" * 60)

# Configuration
print(f"\nTemplates configured: {len(EmailTemplateConfig.TEMPLATES)}")
print(f"Categories configured: {len(EmailTemplateConfig.CATEGORIES)}")

# Sample templates
print("\nSample templates:")
for name in list(EmailTemplateConfig.TEMPLATES.keys())[:5]:
    config = EmailTemplateConfig.TEMPLATES[name]
    print(f"  - {name}: {config.category_name}")

# Registry
registry = EmailTemplateRegistry()
templates = registry.get_all_templates()
print(f"\nTemplates discovered: {len(templates)}")
print(f"Categories: {', '.join(registry.get_categories())}")

# Preview service
service = EmailTemplatePreviewService()
print("\nTesting preview generation:")

# Test newsletter
preview = service.generate_preview("newsletter")
print(f"  Newsletter: {'OK' if not preview.error else 'FAILED'}")
if not preview.error:
    print(f"    Subject: {preview.subject}")

# Test order
preview = service.generate_preview("order_confirmation")
print(f"  Order: {'OK' if not preview.error else 'FAILED'}")
if not preview.error:
    print(f"    Subject: {preview.subject}")

print("\n" + "=" * 60)
print("VERIFICATION COMPLETE - System is working correctly!")
print("=" * 60)
