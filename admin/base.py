"""Shared base classes for admin module-admins.

Centralises unfold attribute defaults so every ModelAdmin in the
project inherits a consistent UX without each file having to re-set
the same flags. Every first-party admin extends ``BaseModelAdmin`` (or
``BaseTranslatableAdmin`` for parler models).

Example:

    from admin.base import BaseModelAdmin

    @admin.register(Foo)
    class FooAdmin(BaseModelAdmin):
        list_display = (...)
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps

from parler.admin import TranslatableAdmin, TranslatableTabularInline
from tinymce.models import HTMLField
from tinymce.widgets import AdminTinyMCE
from unfold.admin import BaseInlineMixin, ModelAdmin
from unfold.mixins import FormFieldModelAdminMixin

from admin.mixins import WithheldOnTenantHostModelAdmin


def _own_copy(method: Callable) -> Callable:
    """A per-class wrapper around an inherited action function."""

    @wraps(method)
    def action(*args, **kwargs):
        return method(*args, **kwargs)

    return action


class BaseModelAdmin(WithheldOnTenantHostModelAdmin, ModelAdmin):
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

    # Every admin action here requires the model's `change` permission
    # unless it declares its own.
    #
    # Django and unfold both FAIL OPEN on an action with no
    # `allowed_permissions`: Django's `_filter_actions_by_permissions`
    # keeps it, and unfold's `_filter_unfold_actions_by_permissions`
    # appends it unconditionally —
    #
    #     if not hasattr(action.method, "allowed_permissions"):
    #         filtered_actions.append(action)
    #         continue
    #
    # — so an undeclared action is offered to anyone who passes the
    # admin site's own gate. Worse for `actions_detail`, which unfold
    # registers as real URLs wrapped only in `admin_site.admin_view`
    # (active + staff), so a member with no model permissions at all
    # could GET one directly. 80 actions across this codebase declared
    # nothing; the mechanism was available and simply unused.
    #
    # `change` rather than `view` is deliberate: it is the restrictive
    # direction, and it is right for the ones that matter (cancel a
    # parcel, publish a post, purge a payout). A genuinely read-only
    # action declares `permissions=["view"]` and says so.
    #
    # An inherited action (a mixin's, a third-party base's) is one
    # function object shared by every class that inherits it. Stamping it
    # in place from the first subclass silently set the policy for all
    # of them, so an inherited undeclared action gets its OWN stamped
    # wrapper on the inheriting class instead.
    _DEFAULT_ACTION_PERMISSIONS = ("change",)

    _ACTION_ATTRIBUTES = (
        "actions",
        "actions_list",
        "actions_detail",
        "actions_row",
        "actions_submit_line",
    )

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        for attribute in cls._ACTION_ATTRIBUTES:
            for name in getattr(cls, attribute, None) or []:
                if not isinstance(name, str):
                    continue
                method = getattr(cls, name, None)
                if method is None or getattr(
                    method, "allowed_permissions", None
                ):
                    continue
                if name not in cls.__dict__:
                    method = _own_copy(method)
                    setattr(cls, name, method)
                method.allowed_permissions = list(
                    cls._DEFAULT_ACTION_PERMISSIONS
                )

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


class BaseTranslatableAdmin(TranslatableAdmin, BaseModelAdmin):
    """Canonical base for django-parler translated admins.

    MRO puts parler FIRST: parler owns the view/URL/form machinery
    (``get_urls``, ``get_form``, language tabs, delete-translation
    views) while unfold only contributes ``formfield_for_dbfield`` and
    templates — which parler does not define, so both cooperate
    cleanly. Every translated admin in the project must extend this
    class instead of hand-mixing the two bases (the codebase previously
    carried three different orderings).
    """


# Suppression is a stubs artefact, not a real override conflict: the two
# bases declare the method with signatures that django-stubs types
# differently, and ty reports the pair rather than a mismatch in the code
# written here.
class BaseTranslatableTabularInline(  # ty: ignore[invalid-method-override]
    BaseInlineMixin, FormFieldModelAdminMixin, TranslatableTabularInline
):
    """Unfold-styled tabular inline for parler-translated child rows.

    Mirrors ``unfold.admin.TabularInline``'s composition
    (``BaseInlineMixin`` for the unfold inline options such as
    ``per_page``/``collapsible``/``show_count``, plus
    ``FormFieldModelAdminMixin`` for unfold form widgets) on top of
    parler's ``TranslatableTabularInline`` so translated inlines render
    with the same chrome as every other inline.
    """
