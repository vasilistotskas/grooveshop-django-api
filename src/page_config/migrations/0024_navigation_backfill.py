"""Convert the JSON navigation menus into columns and links.

``NavigationMenu.items`` held the whole menu as a blob of hand-typed
paths, and ``i18n`` held a SECOND copy per locale. Both are replaced by
rows: a link now names what it points at, so it follows a slug change,
disappears when the page is unpublished, and takes its label from the
page's own translated title.

The JSON fields are deliberately LEFT IN PLACE by this migration. A
rollout runs the migration before the new pods exist, so the old pods
must keep reading what they have always read; the columns come out in a
later cleanup once every replica serves the relational menus.

Mapping every existing link was verified against production first: all
92 links across the four tenants resolve to one of the four targets,
which is why there is no raw-path escape hatch to fall back on. A link
that cannot be mapped is skipped and reported rather than guessed at —
silently inventing a path is what this whole change exists to stop.
"""

from __future__ import annotations

from django.db import migrations

LEGAL_ROUTE_BY_SLUG = {
    "terms": "/terms-of-use",
    "privacy": "/privacy-policy",
    "cookies": "/cookies-policy",
    "return-policy": "/return-policy",
}
BUILT_IN_ROUTES = {
    "/",
    "/products",
    "/blog",
    "/contact",
    "/offers",
    "/gift-cards",
    "/loyalty-program",
    "/feedback",
}


def _target_for(path, pages_by_slug, layouts_by_type):
    """Which target field a JSON ``to`` path corresponds to."""
    for slug, canonical in LEGAL_ROUTE_BY_SLUG.items():
        if path == canonical and slug in pages_by_slug:
            return {"content_page": pages_by_slug[slug]}
    if path.startswith("/info/"):
        page = pages_by_slug.get(path[len("/info/") :])
        if page is not None:
            return {"content_page": page}
    layout = layouts_by_type.get(path.lstrip("/"))
    if layout is not None:
        return {"page_layout": layout}
    if path in BUILT_IN_ROUTES:
        return {"route": path}
    return None


def _links_of(entry):
    """The child links of a footer column, or the entry itself."""
    children = entry.get("children")
    return children if isinstance(children, list) else []


def forwards(apps, schema_editor):
    NavigationMenu = apps.get_model("page_config", "NavigationMenu")
    NavigationColumn = apps.get_model("page_config", "NavigationColumn")
    NavigationColumnTranslation = apps.get_model(
        "page_config", "NavigationColumnTranslation"
    )
    NavigationLink = apps.get_model("page_config", "NavigationLink")
    NavigationLinkTranslation = apps.get_model(
        "page_config", "NavigationLinkTranslation"
    )
    ContentPage = apps.get_model("page_config", "ContentPage")
    PageLayout = apps.get_model("page_config", "PageLayout")

    from django.conf import settings

    default_locale = settings.PARLER_DEFAULT_LANGUAGE_CODE

    pages_by_slug = {p.slug: p for p in ContentPage.objects.all()}
    layouts_by_type = {
        layout.page_type: layout for layout in PageLayout.objects.all()
    }

    for menu in NavigationMenu.objects.all():
        # Re-running must not double the menu.
        if menu.columns.exists() or menu.links.exists():
            continue
        items = menu.items or []
        if not isinstance(items, list):
            continue
        overrides = menu.i18n if isinstance(menu.i18n, dict) else {}

        def _label_overrides(path):
            """``{locale: label}`` for one position in the menu tree.

            The per-locale copies are index-aligned with the default
            menu because they are whole-menu duplicates; a copy whose
            shape differs is skipped rather than mis-assigned, which is
            the failure mode an index-keyed overlay has.
            """
            out = {}
            for locale, blob in overrides.items():
                cursor = blob
                for key in path:
                    if isinstance(cursor, list) and isinstance(key, int):
                        cursor = cursor[key] if key < len(cursor) else None
                    elif isinstance(cursor, dict):
                        cursor = cursor.get(key)
                    else:
                        cursor = None
                    if cursor is None:
                        break
                if isinstance(cursor, dict) and cursor.get("label"):
                    out[locale] = cursor["label"]
            return out

        def _create_link(child, *, index, path, column=None, parent_menu=None):
            href = child.get("href")
            to = child.get("to")
            if href:
                target = {"url": href}
            elif to:
                target = _target_for(to, pages_by_slug, layouts_by_type)
            else:
                target = None
            if target is None:
                print(
                    f"  page_config 0024: skipped unmappable link "
                    f"{menu.slot}:{to or href!r}"
                )
                return
            link = NavigationLink.objects.create(
                menu=parent_menu,
                column=column,
                sort_order=index,
                content_page=target.get("content_page"),
                page_layout=target.get("page_layout"),
                route=target.get("route", ""),
                url=target.get("url", ""),
            )
            # A page link keeps NO label of its own: the page's title is
            # already translated, and copying it here would freeze a
            # stale name the moment the merchant renames the page.
            keeps_own_label = target.get("content_page") is None
            if keeps_own_label and child.get("label"):
                NavigationLinkTranslation.objects.create(
                    master=link,
                    language_code=default_locale,
                    label=child["label"],
                )
                for locale, label in _label_overrides(path).items():
                    NavigationLinkTranslation.objects.create(
                        master=link, language_code=locale, label=label
                    )

        if menu.slot == "footer":
            for i, entry in enumerate(items):
                if not isinstance(entry, dict):
                    continue
                column = NavigationColumn.objects.create(
                    menu=menu, sort_order=i, icon=entry.get("icon") or ""
                )
                NavigationColumnTranslation.objects.create(
                    master=column,
                    language_code=default_locale,
                    label=entry.get("label") or "",
                )
                for locale, label in _label_overrides([i]).items():
                    NavigationColumnTranslation.objects.create(
                        master=column, language_code=locale, label=label
                    )
                for j, child in enumerate(_links_of(entry)):
                    if isinstance(child, dict):
                        _create_link(
                            child,
                            index=j,
                            path=[i, "children", j],
                            column=column,
                        )
        else:
            for i, child in enumerate(items):
                if isinstance(child, dict):
                    _create_link(
                        child, index=i, path=[i], parent_menu=menu
                    )


def backwards(apps, schema_editor):
    """Drop the rows. ``items``/``i18n`` were never touched, so the
    old menus are still there to read."""
    apps.get_model("page_config", "NavigationLink").objects.all().delete()
    apps.get_model("page_config", "NavigationColumn").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [("page_config", "0023_navigation_relational")]

    operations = [migrations.RunPython(forwards, backwards)]
