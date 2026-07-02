"""Custom uvicorn worker for the gunicorn ASGI deployment.

Deliberately a top-level module, NOT part of the ``asgi`` package: the
gunicorn master imports the worker class before forking, and importing
``asgi`` would run ``get_asgi_application()`` (full Django setup) in
the master — accidental app preloading, which is unsafe to fork around
event loops. This module may import only fork-safe, framework-free
code.

``UvicornWorker.CONFIG_KWARGS`` only sets ``loop``/``http``, leaving
uvicorn's ``lifespan`` at its ``auto`` default — under which every
worker boot raises (and swallows) a ``ValueError`` from Channels'
``ProtocolTypeRouter``, which has no ``lifespan`` handler. Subclassing
is the documented uvicorn pattern for overriding worker config; ``ws``
is pinned to the ``websockets`` implementation explicitly rather than
relying on ``auto`` detection.
"""

from uvicorn_worker import UvicornWorker


class GrooveshopUvicornWorker(UvicornWorker):
    CONFIG_KWARGS = {
        **UvicornWorker.CONFIG_KWARGS,
        "lifespan": "off",
        "ws": "websockets",
    }
