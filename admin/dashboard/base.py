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
Axis and legend text take the configured ``base-400``. When
``data-options`` is present Unfold uses it INSTEAD of its defaults, so
every option a chart needs is spelled out here, JSON only (no JS
callbacks survive ``json.dumps``).
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
    every request - labels, URLs and chart JSON are never cached.
    """

    query: DashboardQuery | None = None

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        data = self.query.get() if self.query is not None else None
        context.update(self.shape(data))
        return context

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


def color(token: str) -> str:
    """A theme colour, e.g. ``color("green-500")``."""
    return f"var(--color-{token})"


def _text_color() -> str:
    return get_config()["COLORS"]["base"]["400"]


def chart_json(value: dict[str, Any]) -> str:
    return json.dumps(value)


def _legend(position: str) -> dict[str, Any]:
    return {
        "display": True,
        "position": position,
        "align": "end" if position == "top" else "center",
        "labels": {
            "color": _text_color(),
            "boxWidth": 8,
            "boxHeight": 8,
            "padding": 12,
            "usePointStyle": True,
        },
    }


def _axis(**extra: Any) -> dict[str, Any]:
    axis: dict[str, Any] = {
        "beginAtZero": True,
        "border": {"display": False},
        "ticks": {"color": _text_color(), "precision": 0},
    }
    axis.update(extra)
    return axis


def bar_chart_options(
    *, legend: bool, currency_axis: bool, stacked: bool = False
) -> dict[str, Any]:
    """Options for a bar chart, optionally with a line on a € axis.

    ``currency_axis`` adds a right-hand ``y1`` axis formatted through
    Chart.js's JSON-safe ``Intl.NumberFormat`` passthrough. Both min
    AND max fraction digits are set: Chart.js merges its own minimum
    into the spec, and min > max throws a RangeError that aborts
    ``renderCharts`` for every chart on the page.
    """
    scales: dict[str, Any] = {
        "x": {
            "stacked": stacked,
            "grid": {"display": False},
            "border": {"display": False},
            "ticks": {"color": _text_color(), "maxRotation": 0},
        },
        "y": _axis(position="left", stacked=stacked),
    }
    if currency_axis:
        scales["y1"] = _axis(
            position="right",
            grid={"drawOnChartArea": False},
            ticks={
                "color": _text_color(),
                "format": {
                    "style": "currency",
                    "currency": "EUR",
                    "minimumFractionDigits": 0,
                    "maximumFractionDigits": 0,
                },
            },
        )
    return {
        "responsive": True,
        "maintainAspectRatio": False,
        "animation": False,
        "interaction": {"mode": "index", "intersect": False},
        "plugins": {
            "legend": _legend("top") if legend else {"display": False},
            "tooltip": {"enabled": True, "padding": 10},
        },
        "scales": scales,
    }


def doughnut_chart(
    labels: list[str], values: list[int], colors: list[str]
) -> dict[str, str]:
    """``data``/``options`` JSON for the project doughnut component.

    No border: the gaps (``spacing``) show the card behind the chart,
    so the slices separate in both themes without a colour that only
    suits one of them.
    """
    data = {
        "labels": labels,
        "datasets": [
            {
                "data": values,
                "backgroundColor": colors,
                "borderWidth": 0,
                "spacing": 2,
                "hoverOffset": 6,
            }
        ],
    }
    options = {
        "responsive": True,
        "maintainAspectRatio": False,
        "animation": False,
        "cutout": "68%",
        "plugins": {
            "legend": _legend("bottom"),
            "tooltip": {"enabled": True},
        },
    }
    return {"data": chart_json(data), "options": chart_json(options)}
