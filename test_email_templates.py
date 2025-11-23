#!/usr/bin/env python
"""Test script to generate email template previews."""

import os
import django

# Setup Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")
django.setup()

from core.email.preview_service import EmailTemplatePreviewService  # noqa: E402
from core.email.registry import EmailTemplateRegistry  # noqa: E402


def main():
    """Generate previews for all email templates."""
    print("Generating email template previews...")

    # Initialize services
    registry = EmailTemplateRegistry()
    preview_service = EmailTemplatePreviewService()

    # Get all templates
    templates = registry.get_all_templates()

    # Create output directory
    output_dir = "email_previews"
    os.makedirs(output_dir, exist_ok=True)

    # Generate previews for each template
    for template in templates:
        print(f"\nGenerating preview for: {template.name}")

        try:
            # Generate HTML preview
            preview = preview_service.generate_preview(
                template_name=template.name,
                order_id=None,  # Use sample data
            )

            if preview.error:
                print(f"  ERROR: {preview.error}")
                continue

            # Save HTML preview
            html_file = os.path.join(output_dir, f"{template.name}.html")
            with open(html_file, "w", encoding="utf-8") as f:
                f.write(preview.html_content)
            print(f"  ✓ HTML saved to: {html_file}")

            # Save text preview
            txt_file = os.path.join(output_dir, f"{template.name}.txt")
            with open(txt_file, "w", encoding="utf-8") as f:
                f.write(preview.text_content)
            print(f"  ✓ Text saved to: {txt_file}")

        except Exception as e:
            print(f"  ERROR: {str(e)}")

    print(f"\n✓ All previews generated in '{output_dir}/' directory")
    print("✓ Open the HTML files in a browser to verify UI/UX and colors")


if __name__ == "__main__":
    main()
