#!/usr/bin/env python
"""Comprehensive test of email template system."""

import os
import django
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")
django.setup()

from core.email.registry import EmailTemplateRegistry  # noqa: E402
from core.email.preview_service import EmailTemplatePreviewService  # noqa: E402
from core.email.config import EmailTemplateConfig  # noqa: E402

print("=" * 70)
print("COMPREHENSIVE EMAIL TEMPLATE SYSTEM TEST")
print("=" * 70)

# Test 1: Configuration
print("\n1. Configuration Test")
print("-" * 70)
print(f"   Templates configured: {len(EmailTemplateConfig.TEMPLATES)}")
print(f"   Categories configured: {len(EmailTemplateConfig.CATEGORIES)}")

# Test 2: Registry Discovery
print("\n2. Registry Discovery Test")
print("-" * 70)
registry = EmailTemplateRegistry()
templates = registry.get_all_templates()
print(f"   Templates discovered: {len(templates)}")

# Group by category
by_category = {}
for t in templates:
    if t.category not in by_category:
        by_category[t.category] = []
    by_category[t.category].append(t.name)

for category in sorted(by_category.keys()):
    print(f"   {category}: {len(by_category[category])} templates")

# Test 3: Preview Generation
print("\n3. Preview Generation Test")
print("-" * 70)
service = EmailTemplatePreviewService()

test_templates = [
    ("order_confirmation", "Order Lifecycle"),
    ("order_shipped", "Order Lifecycle"),
    ("confirmation", "Subscription"),
    ("inactive_user_email_template", "User Management"),
    ("newsletter", "Marketing"),
]

all_passed = True
for template_name, expected_category in test_templates:
    preview = service.generate_preview(template_name)
    status = "✓" if not preview.error else "✗"
    if preview.error:
        all_passed = False
        print(f"   {status} {template_name}: ERROR - {preview.error}")
    else:
        print(f"   {status} {template_name}: OK")
        print(f"      Subject: {preview.subject}")
        print(f"      HTML size: {len(preview.html_content)} bytes")

# Test 4: Category Paths
print("\n4. Category Path Test")
print("-" * 70)
path_tests = [
    ("order_confirmation", "order"),
    ("confirmation", "subscription"),
    ("inactive_user_email_template", "user"),
    ("newsletter", "marketing"),
]

for template_name, expected_path in path_tests:
    category = EmailTemplateConfig.get_category_for_template(template_name)
    status = "✓" if category == expected_path else "✗"
    if category != expected_path:
        all_passed = False
    print(
        f"   {status} {template_name}: {category} (expected: {expected_path})"
    )

# Test 5: Context Generators
print("\n5. Context Generator Test")
print("-" * 70)
context_tests = [
    ("order_confirmation", "generate_order_context"),
    ("confirmation", "generate_subscription_context"),
    ("inactive_user_email_template", "generate_user_context"),
    ("newsletter", "generate_marketing_context"),
]

for template_name, expected_generator in context_tests:
    generator = EmailTemplateConfig.get_context_generator_for_template(
        template_name
    )
    status = "✓" if generator == expected_generator else "✗"
    if generator != expected_generator:
        all_passed = False
    print(f"   {status} {template_name}: {generator}")

# Test 6: Template Files Exist
print("\n6. Template File Existence Test")
print("-" * 70)
base_path = Path("core/templates/emails")

file_tests = [
    ("order/order_confirmation.html", True),
    ("subscription/confirmation.html", True),
    ("user/inactive_user_email_template.html", True),
    ("marketing/newsletter.html", True),
]

for file_path, should_exist in file_tests:
    full_path = base_path / file_path
    exists = full_path.exists()
    status = "✓" if exists == should_exist else "✗"
    if exists != should_exist:
        all_passed = False
    print(f"   {status} {file_path}: {'EXISTS' if exists else 'MISSING'}")

# Final Result
print("\n" + "=" * 70)
if all_passed:
    print("✓ ALL TESTS PASSED - System is working correctly!")
else:
    print("✗ SOME TESTS FAILED - Please review errors above")
print("=" * 70)
