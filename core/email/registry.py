"""Email template registry for discovering and managing email templates."""

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from django.conf import settings

from core.email.config import EmailTemplateConfig
from order.enum.status import OrderStatus


@dataclass
class EmailTemplateInfo:
    """Information about an email template."""

    name: str
    path: str
    category: str
    description: str
    order_statuses: list[OrderStatus]
    has_html: bool
    has_text: bool
    is_used: bool
    last_modified: datetime


class EmailTemplateRegistry:
    """
    Registry for discovering and managing email templates.

    Uses a class-level cache so the filesystem scan only runs once
    per process, rather than on every admin page load.
    """

    _cached_templates: dict[str, EmailTemplateInfo] | None = None

    def __init__(self):
        if EmailTemplateRegistry._cached_templates is None:
            EmailTemplateRegistry._cached_templates = {}
            self._templates = EmailTemplateRegistry._cached_templates
            self._discover_templates()
        else:
            self._templates = EmailTemplateRegistry._cached_templates

    @classmethod
    def clear_cache(cls):
        """Clear the template cache, forcing a re-scan on next instantiation."""
        cls._cached_templates = None

    def _discover_templates(self) -> None:
        """Scan template directories and build registry."""
        import logging

        logger = logging.getLogger(__name__)

        # Base template directory
        try:
            emails_dir = (
                Path(settings.BASE_DIR) / "core" / "templates" / "emails"
            )
        except Exception as e:
            logger.critical(
                f"Failed to construct template directory path: {e!s}",
                extra={"base_dir": getattr(settings, "BASE_DIR", None)},
                exc_info=True,
            )
            return

        if not emails_dir.exists():
            logger.warning(
                f"Template directory does not exist: {emails_dir}",
                extra={"template_dir": str(emails_dir)},
            )
            return

        # Check directory permissions
        if not os.access(emails_dir, os.R_OK):
            logger.critical(
                f"No read permission for template directory: {emails_dir}",
                extra={
                    "template_dir": str(emails_dir),
                    "permissions": oct(emails_dir.stat().st_mode),
                },
            )
            return

        # Discover templates using configuration
        try:
            # Scan all configured categories
            for (
                category_key,
                category_config,
            ) in EmailTemplateConfig.CATEGORIES.items():
                category_path = (
                    emails_dir / category_config.path
                    if category_config.path
                    else emails_dir
                )

                # Get templates for this category from configuration
                templates_in_category = [
                    config
                    for config in EmailTemplateConfig.TEMPLATES.values()
                    if config.category_name == category_config.name
                ]

                # Build metadata map for this category
                metadata_map = {
                    config.name: {
                        "category": config.category_name,
                        "description": config.description,
                        "statuses": config.order_statuses,
                        "is_used": config.is_used,
                    }
                    for config in templates_in_category
                }

                # Scan templates in this category
                self._scan_category_templates(
                    category_path,
                    category_config.path,
                    metadata_map,
                    logger,
                    recursive=False,
                )

        except Exception as e:
            logger.critical(
                f"Failed to discover templates: {e!s}",
                extra={"emails_dir": str(emails_dir)},
                exc_info=True,
            )

    def _scan_category_templates(
        self,
        template_dir: Path,
        category_path: str,
        metadata_map: dict,
        logger,
        recursive: bool = False,
    ) -> None:
        """Scan templates in a specific category directory."""
        if not template_dir.exists():
            logger.debug(
                f"Template directory does not exist: {template_dir}",
                extra={"template_dir": str(template_dir)},
            )
            return

        try:
            # Get HTML files
            if recursive:
                html_files = list(template_dir.rglob("*.html"))
            else:
                html_files = [
                    f for f in template_dir.glob("*.html") if f.is_file()
                ]

        except Exception as e:
            logger.error(
                f"Failed to list template files in {template_dir}: {e!s}",
                extra={"template_dir": str(template_dir)},
                exc_info=True,
            )
            return

        for html_file in html_files:
            try:
                template_name = html_file.stem
                txt_file = html_file.parent / f"{template_name}.txt"

                metadata = metadata_map.get(
                    template_name,
                    {
                        "category": "Other",
                        "description": "Email template",
                        "statuses": [],
                        "is_used": False,
                    },
                )

                # Get last modified time
                try:
                    last_modified = datetime.fromtimestamp(
                        html_file.stat().st_mtime
                    )
                except (OSError, PermissionError) as e:
                    logger.warning(
                        f"Could not get modification time for {html_file}: {e!s}",
                        extra={"file": str(html_file)},
                    )
                    last_modified = datetime.now()

                # Build template path
                if category_path:
                    template_path = (
                        f"emails/{category_path}/{template_name}.html"
                    )
                else:
                    template_path = f"emails/{template_name}.html"

                template_info = EmailTemplateInfo(
                    name=template_name,
                    path=template_path,
                    category=metadata["category"],
                    description=metadata["description"],
                    order_statuses=metadata["statuses"],
                    has_html=html_file.exists(),
                    has_text=txt_file.exists(),
                    is_used=metadata["is_used"],
                    last_modified=last_modified,
                )

                self._templates[template_name] = template_info

            except Exception as e:
                logger.error(
                    f"Error processing template file {html_file}: {e!s}",
                    extra={"file": str(html_file)},
                    exc_info=True,
                )

    def get_all_templates(self) -> list[EmailTemplateInfo]:
        """Get all registered templates."""
        return list(self._templates.values())

    def get_template(self, name: str) -> Optional[EmailTemplateInfo]:
        """Get specific template by name."""
        return self._templates.get(name)

    def get_by_category(self, category: str) -> list[EmailTemplateInfo]:
        """Get templates by category."""
        return [
            template
            for template in self._templates.values()
            if template.category == category
        ]

    def get_categories(self) -> list[str]:
        """Get all template categories."""
        categories = {
            template.category for template in self._templates.values()
        }
        return sorted(categories)

    def get_by_status(self, status: OrderStatus) -> list[EmailTemplateInfo]:
        """Get templates associated with a specific order status."""
        return [
            template
            for template in self._templates.values()
            if status in template.order_statuses
        ]
