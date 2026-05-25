"""Shared base classes for admin module-admins.

Centralises unfold attribute defaults so every ModelAdmin in the
project inherits a consistent UX without each file having to re-set
the same six flags. Use ``BaseModelAdmin`` as the base in new admins;
existing admins can migrate incrementally — the class is a strict
super-set, no behaviour changes when applied.

Example:

    from admin.base import BaseModelAdmin

    @admin.register(Foo)
    class FooAdmin(BaseModelAdmin):
        list_display = (...)
"""

from __future__ import annotations

from tinymce.models import HTMLField
from tinymce.widgets import AdminTinyMCE
from unfold.admin import ModelAdmin


class BaseModelAdmin(ModelAdmin):
    """Project-wide defaults for unfold ModelAdmin.

    The values below are the consensus defaults seen across the 30+
    admins in this project; every admin had been re-setting them by
    hand. Subclasses can override any flag freely.

    Attributes
    ----------
    compressed_fields
        Collapses dense form sections so the changeform fits on one
        screen for typical models.
    warn_unsaved_form
        Shows the "you have unsaved changes" prompt before navigating
        away from a dirty form. Catches accidental closes.
    list_fullwidth
        The changelist uses the full content width (sidebar can still
        collapse independently). Most of our list_displays have 6+
        columns and benefit from the extra width.
    list_filter_submit
        Adds an explicit "Apply" button to the filter sheet so multi-
        filter selections only fire one query, not one per click.
    list_filter_sheet
        Renders filters in unfold's slide-out sheet instead of the
        legacy right-rail. Works better with the dense list_displays
        we use.
    save_on_top
        Mirrors the bottom save button at the top so admins editing
        long change forms don't have to scroll to save.
    list_per_page
        25 is enough to scan a screenful and keeps the changelist
        responsive for very large tables (Order has 600+, Product
        will have 1000s). Override per-admin where needed.
    """

    compressed_fields = True
    warn_unsaved_form = True
    list_fullwidth = True
    list_filter_submit = True
    list_filter_sheet = True
    save_on_top = True
    list_per_page = 25

    # Unfold's FORMFIELD_OVERRIDES maps ``models.TextField`` to
    # ``UnfoldAdminTextareaWidget``. Django admin's MRO-walk in
    # ``formfield_for_dbfield`` matches that override on every
    # ``HTMLField`` (subclass of TextField), wiping the TinyMCE widget
    # that ``HTMLField.formfield()`` would otherwise return — so
    # description fields on Product / BlogPost / Category rendered as
    # plain textareas with no editor, and ``form.media`` never pulled
    # in ``tinymce.min.js``. Putting ``HTMLField`` itself in the
    # overrides dict wins the MRO race (HTMLField is checked before
    # TextField) and restores the rich-text editor across every admin
    # that inherits this base.
    formfield_overrides = {
        HTMLField: {"widget": AdminTinyMCE},
    }
