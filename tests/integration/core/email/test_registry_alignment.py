"""The admin template list must reflect what the code actually sends.

Discovery used to iterate the CONFIGURED category list — three entries
while nine directories existed on disk — so billing, cart, giftcard,
product and shipping_acs were invisible to the admin UI entirely, and a
newly added directory would have been invisible too.

Preview resolution had the matching defect: it inferred a directory
from the template NAME, which only works for ``order_``/``subscription_``
prefixes, and built a root-level path for everything else. Seven live
templates previewed as "Template not found".

Both are now driven by what is actually on disk, so neither can drift
again when a template or directory is added.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.conf import settings

from core.email.registry import EmailTemplateRegistry

EMAIL_ROOT = Path(settings.BASE_DIR) / "core" / "templates" / "emails"


@pytest.fixture(autouse=True)
def _fresh_registry():
    EmailTemplateRegistry.clear_cache()
    yield
    EmailTemplateRegistry.clear_cache()


def _sendable_templates() -> set[str]:
    """Every .html on disk except the shared layout."""
    return {
        path.stem
        for path in EMAIL_ROOT.rglob("*.html")
        if path.parent.name != "base"
    }


class TestDiscoveryFollowsTheFilesystem:
    def test_every_template_on_disk_is_discovered(self):
        discovered = {
            t.name for t in EmailTemplateRegistry().get_all_templates()
        }
        missing = _sendable_templates() - discovered
        assert missing == set(), (
            f"templates on disk but invisible to the admin UI: {sorted(missing)}"
        )

    def test_the_shared_layout_is_not_listed_as_sendable(self):
        discovered = {
            t.name for t in EmailTemplateRegistry().get_all_templates()
        }
        assert "email_base" not in discovered

    def test_no_template_is_filed_under_a_generic_other(self):
        """ "Other" grouped live templates under a useless heading."""
        offenders = [
            t.name
            for t in EmailTemplateRegistry().get_all_templates()
            if t.category == "Other"
        ]
        assert offenders == []

    def test_one_display_category_per_directory(self):
        """A folder must not appear under two headings."""
        by_directory: dict[str, set[str]] = {}
        for info in EmailTemplateRegistry().get_all_templates():
            parts = info.path.split("/")
            directory = parts[1] if len(parts) >= 3 else ""
            by_directory.setdefault(directory, set()).add(info.category)
        split = {d: c for d, c in by_directory.items() if len(c) > 1}
        assert split == {}, f"directories with multiple headings: {split}"


@pytest.mark.django_db
class TestEveryPreviewRenders:
    def test_no_template_previews_as_not_found(self):
        from core.email.preview_service import EmailTemplatePreviewService

        service = EmailTemplatePreviewService()
        broken = []
        for info in EmailTemplateRegistry().get_all_templates():
            preview = service.generate_preview(
                template_name=info.name, order_id=None, language="el"
            )
            if preview.error or "Template not found" in (
                preview.html_content or ""
            ):
                broken.append(info.name)
        assert broken == [], f"previews still failing: {broken}"

    def test_order_templates_preview_with_an_order_in_context(self):
        """Path-only fixes left these rendering USER sample data.

        invoice_issued and admin_new_order would have previewed with no
        order in context at all.
        """
        from core.email.preview_service import EmailTemplatePreviewService

        service = EmailTemplatePreviewService()
        for name in ("invoice_issued", "admin_new_order", "payment_failed"):
            preview = service.generate_preview(
                template_name=name, order_id=None, language="el"
            )
            assert "order" in preview.context_data, (
                f"{name} previewed without an order in context"
            )
