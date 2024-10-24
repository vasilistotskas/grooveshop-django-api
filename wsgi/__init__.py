import io
import os

from django.core.wsgi import get_wsgi_application
from django.utils.functional import SimpleLazyObject

from wsgi.health_check import health_check

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")


def get_allowed_host_lazy():
    from django.conf import settings

    return settings.ALLOWED_HOSTS[0]


application = get_wsgi_application()
application = health_check(application, "/health/")

application(
    {
        "REQUEST_METHOD": "GET",
        "SERVER_NAME": SimpleLazyObject(get_allowed_host_lazy),
        "REMOTE_ADDR": "127.0.0.1",
        "SERVER_PORT": 80,
        "PATH_INFO": "/",
        "wsgi.input": io.BytesIO(b""),
        "wsgi.multiprocess": True,
    },
    lambda x, y: None,
)
