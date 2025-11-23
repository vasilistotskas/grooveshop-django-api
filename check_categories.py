#!/usr/bin/env python
"""Check template categories."""

import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")
django.setup()

from core.email.registry import EmailTemplateRegistry  # noqa: E402

registry = EmailTemplateRegistry()
templates = registry.get_all_templates()

# Group by category
by_category = {}
for t in templates:
    if t.category not in by_category:
        by_category[t.category] = []
    by_category[t.category].append(t.name)

# Display
print("Templates by Category:")
print("=" * 60)
for category in sorted(by_category.keys()):
    print(f"\n{category} ({len(by_category[category])} templates):")
    for name in sorted(by_category[category]):
        print(f"  - {name}")

print("\n" + "=" * 60)
