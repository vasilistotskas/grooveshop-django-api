"""Dashboard widget base and presentation helpers.

A widget is an Unfold component class (``unfold.components``): a
template calls ``{% component "<partial>" with component_class="Name" %}``
and Unfold renders the partial with ``Name(request).get_context_data()``.
Unfold's registry is global and keyed by class NAME, so store widgets
are prefixed ``Store`` and platform ones ``Platform``.

Charts follow the contract of Unfold's bundled ``renderCharts`` (its
``app.js``): a ``<canvas class="chart">`` whose ``data-value`` and
``data-options`` hold JSON. That script resolves ``var(--color-*)`` in
each dataset's ``backgroundColor``/``borderColor`` - and nowhere else -
so series colours are CSS variables that follow the theme, using only
the scales Unfold defines (base, primary, blue, green, orange, red).
Axis text takes the configured ``base-400``. When ``data-options`` is
present Unfold uses it INSTEAD of its defaults, so every option a chart
needs is spelled out here, JSON only (no JS callbacks survive
``json.dumps``). One measure per chart: two scales on one canvas read
as a relationship that is not there.
"""

from __future__ import annotations

import json
from typing import Any

from django.urls import reverse
from django.utils import formats
from unfold.components import BaseComponent
from unfold.settings import get_config

from admin.dashboard.cache import DashboardQuery


class DashboardWidget(BaseComponent):
    """A widget backed by an optional cached query.

    ``shape`` turns the query's plain data into template context on
    every request - labels, URLs and chart JSON are never cached. A
    query with variants is asked for ``variant()``.
    """

    query: DashboardQuery | None = None

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        data = (
            self.query.get(self.variant()) if self.query is not None else None
        )
        context.update(self.shape(data))
        return context

    def variant(self) -> str | None:
        return None

    def shape(self, data: Any) -> dict[str, Any]:
        return {}

    def changelist_url(self, label: str, query: str = "") -> str:
        """``label`` is ``"app_label.ModelName"``; resolved on the site
        serving this request (``admin`` or ``platform_admin``)."""
        app_label, model_name = label.lower().split(".")
        url = reverse(
            f"admin:{app_label}_{model_name}_changelist",
            current_app=getattr(self.request, "current_app", None),
        )
        return f"{url}?{query}" if query else url

    def change_url(self, label: str, pk: Any) -> str:
        app_label, model_name = label.lower().split(".")
        return reverse(
            f"admin:{app_label}_{model_name}_change",
            args=[pk],
            current_app=getattr(self.request, "current_app", None),
        )

    @property
    def is_superuser(self) -> bool:
        user = getattr(self.request, "user", None)
        return bool(
            user is not None and user.is_authenticated and user.is_superuser
        )


def percent(part: float, whole: float) -> float:
    return round(part / whole * 100, 1) if whole else 0.0


def percent_text(value: float, *, signed: bool = False) -> str:
    """``12,5%`` under el, ``12.5%`` under en; ``signed`` adds ``+``."""
    text = formats.number_format(value, decimal_pos=1, use_l10n=True)
    return f"{'+' if signed and value >= 0 else ''}{text}%"


def change_percent(current: float, prior: float) -> float | None:
    """The change from ``prior``; None when there is nothing to compare."""
    return round((current - prior) / prior * 100, 1) if prior else None


def color(token: str) -> str:
    """A theme colour, e.g. ``color("green-500")``."""
    return f"var(--color-{token})"


def _text_color() -> str:
    return get_config()["COLORS"]["base"]["400"]


def chart_json(value: dict[str, Any]) -> str:
    return json.dumps(value)


def bar_chart_options(
    *, legend: bool, stacked: bool = False, currency: bool = False
) -> dict[str, Any]:
    """Options for bars on one axis, optionally stacked.

    ``currency`` formats the axis through Chart.js's JSON-safe
    ``Intl.NumberFormat`` passthrough. Both min AND max fraction digits
    are set: Chart.js merges its own minimum into the spec, and
    min > max throws a RangeError that aborts ``renderCharts`` for every
    chart on the page.
    """
    ticks: dict[str, Any] = {"color": _text_color(), "precision": 0}
    if currency:
        ticks["format"] = {
            "style": "currency",
            "currency": "EUR",
            "minimumFractionDigits": 0,
            "maximumFractionDigits": 0,
        }
    return {
        "responsive": True,
        "maintainAspectRatio": False,
        "animation": False,
        "interaction": {"mode": "index", "intersect": False},
        "plugins": {
            "legend": (
                {
                    "display": True,
                    "position": "top",
                    "align": "end",
                    "labels": {
                        "color": _text_color(),
                        "boxWidth": 8,
                        "boxHeight": 8,
                        "padding": 12,
                        "usePointStyle": True,
                    },
                }
                if legend
                else {"display": False}
            ),
            "tooltip": {"enabled": True, "padding": 10},
        },
        "scales": {
            "x": {
                "stacked": stacked,
                "grid": {"display": False},
                "border": {"display": False},
                "ticks": {
                    "color": _text_color(),
                    "maxRotation": 0,
                    "autoSkip": True,
                    "maxTicksLimit": 8,
                },
            },
            "y": {
                "stacked": stacked,
                "beginAtZero": True,
                "border": {"display": False},
                "ticks": ticks,
            },
        },
    }


def bar_chart(
    labels: list[str], values: list[float], *, label: str, currency: bool
) -> dict[str, str]:
    """``data``/``options`` JSON for one measure as bars."""
    data = {
        "labels": labels,
        "datasets": [
            {
                "label": label,
                "data": values,
                "backgroundColor": color("primary-500"),
                "borderRadius": 4,
                "borderSkipped": "start",
                "maxBarThickness": 32,
            }
        ],
    }
    options = bar_chart_options(legend=False, currency=currency)
    return {"data": chart_json(data), "options": chart_json(options)}


def sparkline(values: list[float]) -> dict[str, str]:
    """``data``/``options`` JSON for a KPI card's trend line: no axes,
    no labels, nothing to hover - the card's figures carry the values."""
    data = {
        "labels": [""] * len(values),
        "datasets": [
            {
                "data": values,
                "borderColor": color("primary-500"),
                "backgroundColor": color("primary-500"),
                "borderWidth": 2,
                "pointRadius": 0,
                "tension": 0.35,
                "fill": False,
            }
        ],
    }
    options = {
        "responsive": True,
        "maintainAspectRatio": False,
        "animation": False,
        "events": [],
        "layout": {"padding": 2},
        "plugins": {
            "legend": {"display": False},
            "tooltip": {"enabled": False},
        },
        "scales": {"x": {"display": False}, "y": {"display": False}},
    }
    return {"data": chart_json(data), "options": chart_json(options)}
