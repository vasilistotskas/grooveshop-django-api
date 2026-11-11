"""Admin dashboards: the store admin's and the control plane's.

Each dashboard is a grid of Unfold components (``unfold.components``)
rendered by ``core/templates/admin/index.html`` and
``core/templates/admin/platform_index.html``. A widget is a
``@register_component`` class feeding one template partial under
``core/templates/admin/dashboard/``; it loads its own data, so neither
site sets ``DASHBOARD_CALLBACK``.

- ``cache``: cached, invalidated queries (``dashboard_query``).
- ``base``: the widget base class and chart/table presentation helpers.
- ``store``: the store admin's queries and widgets.
- ``platform``: the control plane's widgets.
"""
