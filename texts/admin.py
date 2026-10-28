"""The store admin's text editor.

One dedicated page, not a changelist: ``TextOverrideAdmin.changelist_view``
renders the editor, and the other views are the small htmx endpoints it
talks to (validate while typing, save, reset, history, restore). Every
write goes through ``texts.services``; the model has no add/change form,
because a form would bypass the storefront's validation.
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from typing import Any
from urllib.parse import urlencode

from django import forms
from django.contrib import admin
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.decorators import method_decorator
from django.utils.html import format_html
from django.utils.safestring import SafeString
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.http import require_http_methods
from unfold.widgets import (
    UnfoldAdminSelectWidget,
    UnfoldAdminTextareaWidget,
    UnfoldAdminTextInputWidget,
    UnfoldBooleanSwitchWidget,
)

from admin.base import BaseModelAdmin
from core.utils.tenant_urls import get_tenant_frontend_url
from texts import editor, services, storefront
from texts.areas import ALL_AREAS
from texts.models import TextOverride
from texts.validation import check_value, normalise, refusal

logger = logging.getLogger(__name__)

HTMX_HEADER = "HX-Request"


class FilterForm(forms.Form):
    q = forms.CharField(
        required=False,
        label=gettext_lazy("Search"),
        widget=UnfoldAdminTextInputWidget(
            attrs={
                "type": "search",
                "placeholder": gettext_lazy(
                    "Search by text, key or page, in any language"
                ),
                "autocomplete": "off",
                "autofocus": True,
            }
        ),
    )
    area = forms.ChoiceField(
        required=False,
        label=gettext_lazy("Area"),
        widget=UnfoldAdminSelectWidget,
    )
    changed = forms.BooleanField(
        required=False,
        label=gettext_lazy("Changed only"),
        widget=UnfoldBooleanSwitchWidget,
    )
    missing = forms.BooleanField(
        required=False,
        label=gettext_lazy("Missing a translation"),
        widget=UnfoldBooleanSwitchWidget,
    )

    def __init__(self, *args: Any, counts: dict, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        choices = [("", _("All areas"))]
        for area in ALL_AREAS:
            total, changed = counts.get(area.code, (0, 0))
            if not total:
                continue
            label = f"{area.title} ({total})"
            if changed:
                label += " - " + _("%(n)s changed") % {"n": changed}
            choices.append((area.code, label))
        area = self.fields["area"]
        assert isinstance(area, forms.ChoiceField)
        area.choices = choices


@admin.register(TextOverride)
class TextOverrideAdmin(BaseModelAdmin):
    # The editor replaces the changelist; nothing of ModelAdmin's list is used.
    list_display = ("namespace", "key", "language")

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: TextOverride | None = None
    ) -> bool:
        return False

    # ------------------------------------------------------------------ urls

    def get_urls(self):
        info = self.opts.app_label, self.opts.model_name
        wrap = self.admin_site.admin_view
        return [
            path(
                "validate/",
                wrap(self.validate_view),
                name=f"{info[0]}_{info[1]}_validate",
            ),
            path(
                "save/", wrap(self.save_view), name=f"{info[0]}_{info[1]}_save"
            ),
            path(
                "reset/",
                wrap(self.reset_view),
                name=f"{info[0]}_{info[1]}_reset",
            ),
            path(
                "history/",
                wrap(self.history_endpoint),
                name=f"{info[0]}_{info[1]}_history",
            ),
            path(
                "restore/",
                wrap(self.restore_view),
                name=f"{info[0]}_{info[1]}_restore",
            ),
            *super().get_urls(),
        ]

    def _url(self, name: str) -> str:
        return reverse(
            f"admin:{self.opts.app_label}_{self.opts.model_name}_{name}"
        )

    def change_view(self, request, object_id, form_url="", extra_context=None):
        row = self.get_object(request, object_id)
        query = {"q": row.key} if row else {}
        return redirect(f"{self._url('changelist')}?{urlencode(query)}")

    # ------------------------------------------------------------- the page

    def changelist_view(self, request, extra_context=None):
        if not self.has_view_or_change_permission(request):
            raise PermissionDenied
        filters = editor.Filters.from_query(request.GET)
        context: dict[str, Any] = {
            **self.admin_site.each_context(request),
            "title": _("Storefront texts"),
            "opts": self.opts,
            "filters": filters,
            "can_edit": self._can_edit(request),
            "max_length": self._max_length(),
        }
        try:
            catalogue = storefront.fetch_catalogue(self._schema())
        except storefront.StorefrontUnavailable:
            context["unavailable"] = True
            context["form"] = FilterForm(initial=filters.as_query(), counts={})
            template = "admin/texts/editor.html"
            return TemplateResponse(request, template, context)

        overrides = editor.load_overrides()
        form = FilterForm(
            initial={
                **filters.as_query(),
                "changed": filters.changed,
                "missing": filters.missing,
            },
            counts=editor.area_counts(catalogue, overrides),
        )
        entries = editor.select(catalogue, overrides, filters)
        page = Paginator(entries, editor.PER_PAGE).get_page(
            request.GET.get("page")
        )
        context.update(
            form=form,
            page=page,
            rows=[
                self._row(request, entry, catalogue, overrides)
                for entry in page.object_list
            ],
            previous_url=self._page_url(filters, page.previous_page_number())
            if page.has_previous()
            else "",
            next_url=self._page_url(filters, page.next_page_number())
            if page.has_next()
            else "",
            changed_total=len(overrides),
        )
        is_fragment = request.headers.get(
            HTMX_HEADER
        ) and not request.headers.get("HX-History-Restore-Request")
        template = (
            "admin/texts/_results.html"
            if is_fragment
            else "admin/texts/editor.html"
        )
        return TemplateResponse(request, template, context)

    # ----------------------------------------------------------- row helpers

    def _page_url(self, filters: editor.Filters, number: int) -> str:
        query = urlencode({**filters.as_query(), "page": number})
        return f"{self._url('changelist')}?{query}"

    @staticmethod
    def _schema() -> str:
        from django.db import connection

        return connection.schema_name

    @staticmethod
    def _max_length() -> int:
        from django.conf import settings

        return settings.TEXTS_MAX_VALUE_LENGTH

    @staticmethod
    def _can_edit(request: HttpRequest) -> bool:
        return request.user.has_perm(services.CHANGE_PERMISSION)

    def _control(
        self,
        request: HttpRequest,
        row: editor.Row,
        cell: editor.Cell,
    ) -> SafeString:
        """The language's textarea, Unfold's own widget with the htmx
        attributes that validate it while the editor types."""
        locked = row.locked or not self._can_edit(request)
        attrs: dict[str, Any] = {
            "id": f"{row.dom_id}-{cell.language}",
            "rows": 3,
            "lang": cell.language,
            "x-on:input": "n = $el.value.length",
            "hx-post": self._url("validate"),
            "hx-trigger": "input changed delay:500ms",
            "hx-target": f"#check-{row.dom_id}-{cell.language}",
            "hx-include": "closest form",
            "hx-vals": json.dumps({"language": cell.language}),
            "hx-swap": "innerHTML",
        }
        if locked:
            attrs["disabled"] = True
            attrs.pop("hx-post")
        return UnfoldAdminTextareaWidget(attrs=attrs).render(
            f"value_{cell.language}", cell.value
        )

    def _row(
        self,
        request: HttpRequest,
        entry: storefront.CatalogueEntry,
        catalogue: storefront.Catalogue,
        overrides: editor.Overrides,
        *,
        posted: dict[str, str] | None = None,
        errors: dict[str, ValidationError] | None = None,
    ) -> editor.Row:
        row = editor.build_row(
            entry,
            catalogue.locales,
            overrides.get((entry.namespace, entry.key), {}),
        )
        cells = []
        for cell in row.cells:
            if posted and cell.language in posted:
                cell = replace(cell, value=posted[cell.language])
            cells.append(
                replace(
                    cell,
                    control=self._control(request, row, cell),
                    error=(errors or {}).get(cell.language),
                )
            )
        preview = None
        if row.area.preview_path:
            preview = get_tenant_frontend_url(
                row.area.preview_path,
                language=editor.preview_language(catalogue.locales),
            )
        return replace(
            row,
            cells=tuple(cells),
            preview_url=preview,
            reset_attrs=format_html(
                'hx-post="{}" hx-vals="{}" hx-target="closest article" '
                'hx-swap="outerHTML" hx-confirm="{}"',
                self._url("reset"),
                row.vals,
                _("Remove this store's wording and go back to the default?"),
            ),
            history_attrs=format_html(
                'hx-get="{}" hx-vals="{}" hx-target="#history-{}" '
                'hx-swap="innerHTML"',
                self._url("history"),
                row.vals,
                row.dom_id,
            ),
        )

    def _render_row(
        self,
        request: HttpRequest,
        namespace: str,
        key: str,
        *,
        posted: dict[str, str] | None = None,
        errors: dict[str, ValidationError] | None = None,
        notice: str = "",
    ) -> HttpResponse:
        try:
            catalogue = storefront.fetch_catalogue(self._schema())
        except storefront.StorefrontUnavailable:
            return self._unavailable_response(request)
        entry = catalogue.get(namespace, key)
        if entry is None:
            return HttpResponse(
                _("This text no longer exists in the storefront."), status=404
            )
        overrides = editor.load_overrides()
        row = self._row(
            request, entry, catalogue, overrides, posted=posted, errors=errors
        )
        return TemplateResponse(
            request,
            "admin/texts/_row.html",
            {
                "row": row,
                "notice": notice,
                "can_edit": self._can_edit(request),
                "max_length": self._max_length(),
                "opts": self.opts,
            },
        )

    @staticmethod
    def _unavailable_response(request: HttpRequest) -> HttpResponse:
        """The storefront could not answer: say so in the page's alert
        region (htmx does not swap error statuses, so this is a 200
        retargeted by header)."""
        response = TemplateResponse(
            request, "admin/texts/_unavailable.html", {}
        )
        response["HX-Retarget"] = "#texts-alert"
        response["HX-Reswap"] = "innerHTML"
        return response

    @staticmethod
    def _text_id(request: HttpRequest) -> tuple[str, str]:
        namespace = request.POST.get("namespace") or request.GET.get(
            "namespace", ""
        )
        key = request.POST.get("key") or request.GET.get("key", "")
        return namespace, key

    # -------------------------------------------------------------- endpoints

    @method_decorator(require_http_methods(["POST"]))
    def validate_view(self, request: HttpRequest) -> HttpResponse:
        """The check under a textarea, while the editor types."""
        services.require_change_permission(request.user)
        namespace, key = self._text_id(request)
        language = request.POST.get("language", "")
        value = request.POST.get(f"value_{language}", "")
        error: ValidationError | None = None
        shown = False
        try:
            catalogue = storefront.fetch_catalogue(self._schema())
            entry = catalogue.get(namespace, key)
            wanted = normalise(value)
            same_as_default = entry is not None and (
                entry.defaults.get(language) == wanted
            )
            if wanted and not same_as_default:
                check_value(
                    namespace=namespace,
                    key=key,
                    language=language,
                    value=value,
                    schema=self._schema(),
                )
                shown = True
        except storefront.StorefrontUnavailable:
            error = refusal("storefront_unavailable")
        except ValidationError as exc:
            error = exc
        return TemplateResponse(
            request,
            "admin/texts/_check.html",
            {"error": error, "valid": shown and error is None},
        )

    @method_decorator(require_http_methods(["POST"]))
    def save_view(self, request: HttpRequest) -> HttpResponse:
        namespace, key = self._text_id(request)
        try:
            catalogue = storefront.fetch_catalogue(self._schema())
        except storefront.StorefrontUnavailable:
            return self._unavailable_response(request)
        posted = {
            language: request.POST[f"value_{language}"]
            for language in catalogue.locales
            if f"value_{language}" in request.POST
        }
        try:
            result = services.save_texts(
                request.user, namespace=namespace, key=key, values=posted
            )
        except ValidationError as exc:
            result = services.SaveResult(
                errors={language: exc for language in posted}
            )
        if not result.ok:
            return self._render_row(
                request,
                namespace,
                key,
                posted=posted,
                errors=result.errors,
                notice=_("Nothing was saved. Fix the marked texts."),
            )
        done = result.saved or result.reset
        return self._render_row(
            request,
            namespace,
            key,
            notice=_("Saved.") if done else _("No changes to save."),
        )

    @method_decorator(require_http_methods(["POST"]))
    def reset_view(self, request: HttpRequest) -> HttpResponse:
        namespace, key = self._text_id(request)
        services.reset_text(request.user, namespace=namespace, key=key)
        return self._render_row(
            request, namespace, key, notice=_("Back to the default text.")
        )

    @method_decorator(require_http_methods(["GET"]))
    def history_endpoint(self, request: HttpRequest) -> HttpResponse:
        if not self.has_view_or_change_permission(request):
            raise PermissionDenied
        namespace, key = self._text_id(request)
        try:
            catalogue = storefront.fetch_catalogue(self._schema())
        except storefront.StorefrontUnavailable:
            return self._unavailable_response(request)
        entry = catalogue.get(namespace, key)
        if entry is None:
            return HttpResponse(status=404)
        return TemplateResponse(
            request,
            "admin/texts/_history.html",
            {
                "items": [
                    (
                        item,
                        format_html(
                            'hx-post="{}" hx-vals="{}" hx-target="#{}" '
                            'hx-swap="outerHTML"',
                            self._url("restore"),
                            json.dumps(
                                {
                                    "namespace": namespace,
                                    "key": key,
                                    "history_id": item.history_id,
                                }
                            ),
                            editor.row_id(namespace, key),
                        ),
                    )
                    for item in editor.history_for(entry)
                ],
                "can_edit": self._can_edit(request),
                "opts": self.opts,
            },
        )

    @method_decorator(require_http_methods(["POST"]))
    def restore_view(self, request: HttpRequest) -> HttpResponse:
        namespace, key = self._text_id(request)
        try:
            history_id = int(request.POST.get("history_id", ""))
        except ValueError:
            return HttpResponse(status=400)
        try:
            services.restore_text(request.user, history_id=history_id)
        except ValidationError as exc:
            return self._render_row(
                request,
                namespace,
                key,
                notice=" ".join(exc.messages),
            )
        return self._render_row(request, namespace, key, notice=_("Restored."))
