"""The project settings (``DJANGO_SETTINGS_MODULE=settings``).

Each module holds one concern; this package exports them all, in the
order below. The order is the order the single ``settings.py`` ran its
statements in wherever one had an effect beyond its own value: the
``.env`` load first, then the boot checks that refuse a misconfigured
deployment in the order they always ran, the log directory created
after the mail checks and before the carrier ones. A module that
needs an earlier value imports it by name from the module that
defines it; none imports a later one.
"""

from settings.env import *
from settings.application import *
from settings.auth import *
from settings.i18n import *
from settings.api import *
from settings.cache import *
from settings.websocket import *
from settings.celery import *
from settings.schedule import *
from settings.security import *
from settings.database import *
from settings.search import *
from settings.store_settings import *
from settings.email import *
from settings.admin import *
from settings.storage import *
from settings.logging import *
from settings.integrations import *
from settings.domain import *
