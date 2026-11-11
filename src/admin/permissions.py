from django.utils.translation import gettext_lazy as _


def is_superuser(request) -> bool:
    user = getattr(request, "user", None)
    return bool(user and user.is_authenticated and user.is_superuser)


def opens_changelist(request, model_admin) -> bool:
    """Whether this user may open the model's changelist: the same two
    checks Django's own app list applies. The module is where a
    platform-only model is withheld on a store host and a switched-off
    feature's page is refused (``FeatureGatedModelAdmin``); the second is
    the view-or-change permission."""
    return bool(
        model_admin is not None
        and model_admin.has_module_permission(request)
        and model_admin.has_view_or_change_permission(request)
    )


def platform_environment(request) -> list[str] | None:
    """Header badge marking the control plane.

    Makes it unmistakable which console you are in — the two look alike
    enough that an operator could otherwise edit the wrong thing.
    """
    return [_("Control plane"), "warning"]
