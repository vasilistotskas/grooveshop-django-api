from django.utils.translation import gettext_lazy as _


def is_superuser(request) -> bool:
    user = getattr(request, "user", None)
    return bool(user and user.is_authenticated and user.is_superuser)


def platform_environment(request) -> list[str] | None:
    """Header badge marking the control plane.

    Makes it unmistakable which console you are in — the two look alike
    enough that an operator could otherwise edit the wrong thing.
    """
    return [_("Control plane"), "warning"]
