def is_superuser(request) -> bool:
    user = getattr(request, "user", None)
    return bool(user and user.is_authenticated and user.is_superuser)


def is_staff(request) -> bool:
    user = getattr(request, "user", None)
    return bool(user and user.is_authenticated and user.is_staff)
