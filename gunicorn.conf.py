"""Gunicorn configuration for the production ASGI deployment.

Runs the Channels ``ProtocolTypeRouter`` (``asgi:application``) under
gunicorn's process manager with uvicorn workers — one event loop per
worker process. Replaces the single-process daphne CMD whose lone
event loop meant any CPU-bound request stalled every concurrent
request, including the Kubernetes health probes (prod incident
2026-07-02: both pods failed readiness simultaneously under CPU load).

Worker count comes from ``WEB_CONCURRENCY`` so the deployment manifest
stays the single source of truth for per-pod capacity.
"""

import os

bind = "0.0.0.0:8000"
workers = int(os.environ.get("WEB_CONCURRENCY", "2"))
worker_class = "gunicorn_worker.GrooveshopUvicornWorker"

# UvicornWorker heartbeats via uvicorn's callback_notify, which runs
# on the worker's event loop — slow-but-yielding requests keep the
# heartbeat alive, while a genuinely wedged loop stops notifying and
# the master kills/restarts just that worker instead of Kubernetes
# restarting the whole pod.
timeout = 60
graceful_timeout = 30

# Passed through to uvicorn as timeout_keep_alive. Must outlive the
# Nuxt SSR undici pool's 30s client-side keep-alive so the client
# always closes idle connections first (avoids close/reuse races).
keepalive = 65

# No control socket (gunicornc runtime management, gunicorn 25.1+):
# unused here, and its default path ($HOME/.gunicorn/gunicorn.ctl)
# fails on the pod's read-only root filesystem with a boot-time
# "Control server error: [Errno 30] Read-only file system".
control_socket_disable = True

# Worker lifecycle events to stderr; per-request access logging stays
# off — Django logging owns the request trail.
errorlog = "-"
accesslog = None
