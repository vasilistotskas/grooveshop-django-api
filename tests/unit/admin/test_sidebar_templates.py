"""Our nested-sidebar override of Unfold's ``app_list.html``.

``core/templates/unfold/helpers/app_list.html`` is upstream's template
plus ONE delta (items render through the recursive
``app_list_item.html``). These tests pin both halves: the nesting we
add, and the upstream features the override must keep carrying.
"""

from __future__ import annotations

from django.template.loader import render_to_string


def _leaf(title: str, **extra) -> dict:
    return {
        "title": title,
        "link": f"/admin/{title.lower()}/",
        "has_permission": True,
        "active": False,
        **extra,
    }


def _render(items: list[dict]) -> str:
    return render_to_string(
        "unfold/helpers/app_list.html",
        {"sidebar_navigation": [{"title": "Group", "items": items}]},
    )


def test_nested_items_render_as_a_subtree():
    html = _render(
        [
            {
                "title": "Audit",
                "has_permission": True,
                "items": [_leaf("History"), _leaf("Logs")],
            }
        ]
    )
    assert "subtreeOpen" in html
    assert 'href="/admin/history/"' in html
    assert 'href="/admin/logs/"' in html


def test_children_without_permission_are_not_rendered():
    html = _render(
        [
            {
                "title": "Audit",
                "has_permission": True,
                "items": [_leaf("History", has_permission=False)],
            }
        ]
    )
    assert "/admin/history/" not in html


def test_link_attrs_reach_the_anchor():
    html = _render(
        [_leaf("Docs", link_attrs={"target": "_blank", "rel": "noopener"})]
    )
    assert 'target="_blank"' in html
    assert 'rel="noopener"' in html


def test_an_empty_badge_renders_nothing():
    html = _render([_leaf("Orders", badge="x", badge_callback=None)])
    assert ">None<" not in html


def test_the_override_keeps_upstreams_scroll_container():
    html = _render([_leaf("Orders")])
    assert "scrollbar-default-hover" in html
    assert "data-simplebar" not in html
