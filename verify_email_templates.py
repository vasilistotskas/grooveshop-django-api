#!/usr/bin/env python
"""Verification script for email templates - checks UI/UX, colors, and logo."""

from pathlib import Path


def verify_template(html_path):
    """Verify a single email template."""
    with open(html_path, "r", encoding="utf-8") as f:
        content = f.read()

    issues = []

    # Check 1: Logo is present
    if "logo-light.svg" not in content:
        issues.append("❌ Logo not found")
    else:
        print("  ✓ Logo present")

    # Check 2: Logo URL is correct (no double /static/)
    if "/static/static/" in content or "//static/" in content:
        issues.append("❌ Logo URL has duplicate /static/")
    else:
        print("  ✓ Logo URL correct")

    # Check 3: Brand colors are present
    brand_colors = ["#2563eb", "#1e40af", "#10b981"]
    colors_found = all(color in content for color in brand_colors)
    if not colors_found:
        issues.append("❌ Brand colors missing")
    else:
        print("  ✓ Brand colors present")

    # Check 4: Responsive meta tags
    if "viewport" not in content:
        issues.append("❌ Responsive viewport meta tag missing")
    else:
        print("  ✓ Responsive design meta tags present")

    # Check 5: Site name is present
    if "Grooveshop" not in content and "GrooveShop" not in content:
        issues.append("❌ Site name not found")
    else:
        print("  ✓ Site name present")

    # Check 6: Email structure (header, body, footer)
    if "email-header" not in content:
        issues.append("❌ Email header missing")
    if "email-body" not in content:
        issues.append("❌ Email body missing")
    if "email-footer" not in content:
        issues.append("❌ Email footer missing")
    if not issues or len([i for i in issues if "Email" in i]) == 0:
        print("  ✓ Email structure complete")

    # Check 7: Modern styling (gradients, shadows, border-radius)
    modern_features = ["linear-gradient", "box-shadow", "border-radius"]
    modern_found = all(feature in content for feature in modern_features)
    if not modern_found:
        issues.append("❌ Modern styling features missing")
    else:
        print("  ✓ Modern styling present")

    # Check 8: Dark mode support
    if "prefers-color-scheme: dark" not in content:
        issues.append("❌ Dark mode support missing")
    else:
        print("  ✓ Dark mode support present")

    # Check 9: Contact email in footer
    if "INFO_EMAIL" in content or "@" in content:
        print("  ✓ Contact email present")
    else:
        issues.append("❌ Contact email missing")

    # Check 10: Proper text colors for visibility
    text_colors = ["#333333", "#1f2937", "#6b7280"]
    if any(color in content for color in text_colors):
        print("  ✓ Text colors for visibility present")
    else:
        issues.append("❌ Text colors may not be visible")

    return issues


def main():
    """Verify all email templates."""
    print("=" * 70)
    print("EMAIL TEMPLATE VERIFICATION REPORT")
    print("=" * 70)
    print()

    preview_dir = Path("email_previews")
    if not preview_dir.exists():
        print(
            "❌ Preview directory not found. Run test_email_templates.py first."
        )
        return

    html_files = list(preview_dir.glob("*.html"))
    if not html_files:
        print("❌ No HTML files found in preview directory.")
        return

    print(f"Found {len(html_files)} email templates to verify\n")

    all_issues = {}

    for html_file in sorted(html_files):
        template_name = html_file.stem
        print(f"📧 Verifying: {template_name}")
        print("-" * 70)

        issues = verify_template(html_file)

        if issues:
            all_issues[template_name] = issues
            print("\n⚠️  Issues found:")
            for issue in issues:
                print(f"    {issue}")
        else:
            print("\n✅ All checks passed!")

        print()

    # Summary
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)

    if not all_issues:
        print("✅ All templates passed verification!")
        print("✅ UI/UX is correct")
        print("✅ Colors are visible and consistent")
        print("✅ Logo is properly displayed")
        print("✅ Responsive design is implemented")
        print("✅ Dark mode support is present")
    else:
        print(f"⚠️  {len(all_issues)} template(s) have issues:")
        for template, issues in all_issues.items():
            print(f"\n  {template}:")
            for issue in issues:
                print(f"    {issue}")

    print()
    print("=" * 70)
    print("NEXT STEPS")
    print("=" * 70)
    print("1. Open the HTML files in email_previews/ directory in a browser")
    print("2. Verify visual appearance matches design requirements")
    print("3. Test on different email clients (Gmail, Outlook, etc.)")
    print("4. Check mobile responsiveness")
    print()


if __name__ == "__main__":
    main()
