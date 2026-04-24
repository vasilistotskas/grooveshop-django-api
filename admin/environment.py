from django.conf import settings


def environment_callback(request):
    env = (getattr(settings, "SYSTEM_ENV", "") or "").lower()
    if env in ("prod", "production"):
        return ["Production", "danger"]
    if env in ("staging", "stage"):
        return ["Staging", "warning"]
    if env in ("ci", "test"):
        return ["CI", "info"]
    return ["Development", "success"]
