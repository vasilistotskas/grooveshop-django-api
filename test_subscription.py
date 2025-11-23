#!/usr/bin/env python
"""Test subscription confirmation template."""

import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")
django.setup()

from core.email.preview_service import EmailTemplatePreviewService  # noqa: E402

# Generate preview
service = EmailTemplatePreviewService()
preview = service.generate_preview("confirmation")

print("=" * 60)
print("SUBSCRIPTION CONFIRMATION TEMPLATE TEST")
print("=" * 60)

if preview.error:
    print(f"\nERROR: {preview.error}")
else:
    print("\nStatus: SUCCESS")
    print(f"Subject: {preview.subject}")
    print(f"HTML length: {len(preview.html_content)} bytes")
    print(f"Text length: {len(preview.text_content)} bytes")

    # Check for expected content
    checks = {
        "Has 'Premium' plan": "Premium" in preview.html_content,
        "Has user name 'John'": "John" in preview.html_content,
        "Has subscription status": "active" in preview.html_content.lower(),
        "Has amount": "€9.99" in preview.html_content,
        "Has 'Subscription Details'": "Subscription Details"
        in preview.html_content,
    }

    print("\nContent checks:")
    for check, result in checks.items():
        status = "✓" if result else "✗"
        print(f"  {status} {check}")

    # Save preview
    with open(
        "email_previews/confirmation_new.html", "w", encoding="utf-8"
    ) as f:
        f.write(preview.html_content)
    print("\nPreview saved to: email_previews/confirmation_new.html")

print("=" * 60)
