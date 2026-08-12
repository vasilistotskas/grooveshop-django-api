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
is pinned explicitly rather than relying on ``auto`` detection.

``ws`` uses the Sans-I/O implementation. The legacy ``websockets`` value
is deprecated — it logs a ``UvicornDeprecationWarning`` on every worker
boot and will itself point at Sans-I/O in a future uvicorn release, so
pinning it buys no stability. ``auto`` already resolves to the same
``WebSocketsSansIOProtocol`` whenever the ``websockets`` package is
installed; naming the implementation keeps the choice explicit instead
of dependent on which extras happen to be present.
"""

from uvicorn_worker import UvicornWorker


class GrooveshopUvicornWorker(UvicornWorker):
    CONFIG_KWARGS = {
        **UvicornWorker.CONFIG_KWARGS,
        "lifespan": "off",
        "ws": "websockets-sansio",
    }
