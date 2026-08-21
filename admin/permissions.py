def is_superuser(request) -> bool:
    user = getattr(request, "user", None)
    return bool(user and user.is_authenticated and user.is_superuser)


def is_staff(request) -> bool:
    user = getattr(request, "user", None)
    return bool(user and user.is_authenticated and user.is_staff)


def is_store_section(request) -> bool:
    """Show a per-store sidebar group only where its data lives.

    The platform console (public schema) manages tenants, users and
    platform settings — it has no orders, products, blog posts or
    shipments, and ``BaseModelAdmin._withheld_on_public`` already
    answers 403 for those models there. Without this the control plane
    still rendered every store section in the sidebar: 30 links that
    all lead to a 403. Reported from production 2026-08-21.

    Returns True on a tenant host and on an UNKNOWN schema. Hiding on
    unknown would blank the sidebar during tests and management
    commands, the same positive-knowledge rule the model-level guard
    uses.
    """
    from tenant.console import is_platform_console  # noqa: PLC0415

    return not is_platform_console(request)


def is_platform_section(request) -> bool:
    """Show a control-plane-only sidebar group on the platform console.

    The inverse of ``is_store_section`` for groups (tenants, platform
    settings) that make no sense inside a single store's admin.
    """
    from tenant.console import is_platform_console  # noqa: PLC0415

    return is_platform_console(request)
