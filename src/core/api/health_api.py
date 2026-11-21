"""The health endpoints: ``/health`` (database, Redis, Celery) and the
liveness probe ``/health/live``."""

from celery.exceptions import CeleryError
from django.conf import settings
from django.db import DatabaseError, connection
from django.middleware.csrf import get_token
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import (
    extend_schema,
)
from kombu.exceptions import OperationalError as BrokerOperationalError
from redis import Redis, RedisError
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
    throttle_classes,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from core.api.serializers import (
    HealthCheckResponseSerializer,
)
from core.celery import celery_app


def _broker_errors() -> tuple[type[BaseException], ...]:
    """What a ping raises when the broker is down or drops the connection.

    kombu raises its own ``OperationalError`` when it cannot connect, and
    the transport's connection and channel errors from a connection it
    already holds: after a broker restart, the first ping on the pooled
    connection raised ``ConnectionResetError``, and ``health_check``
    answered 500 instead of ``celery: false``.
    """
    with celery_app.connection_for_write() as broker:
        return (
            BrokerOperationalError,
            *broker.connection_errors,
            *broker.channel_errors,
        )


# `@extend_schema` over `@api_view` is drf-spectacular's own documented
# pattern (see its FAQ). The suppressions on the decorators below are a ty
# limitation, not a defect here: `djangorestframework-stubs` declares
# `AsView` as `Protocol[_View]` with `__call__: _View` — an attribute
# annotated with the class's own TypeVar — and ty will not resolve that
# into callability when checking `TypeVar F, bound=Callable[..., Any]`.
# Reduced to 25 lines with no Django involved: the same Protocol with a
# concrete `__call__: Callable[..., Any]` passes, the generic form fails.
# Upstream master still declares it the same way, so a stubs bump does
# not help. Suppressed per line rather than per file so real
# argument-type errors in these modules are still reported.
@extend_schema(  # ty: ignore[invalid-argument-type]
    summary=_("Check the health status of database, Redis, and Celery"),
    description=_("Check the health status of database, Redis, and Celery"),
    tags=["Health"],
    responses=HealthCheckResponseSerializer,
)
@api_view(["GET"])
# No throttling: DRF's default throttles hit the Redis cache before the
# view runs, so a Redis outage would 500 this endpoint — turning the
# dependency check into a hard failure instead of the graceful
# per-service booleans below. Probes also must never consume (or
# exhaust) the shared anonymous throttle bucket.
@throttle_classes([])
def health_check(request):
    health_status = {
        "database": False,
        "redis": False,
        "celery": False,
    }

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        health_status["database"] = True
    except DatabaseError:
        health_status["database"] = False

    try:
        redis_conn = Redis.from_url(settings.REDIS_URL)
        try:
            redis_conn.ping()
            health_status["redis"] = True
        finally:
            redis_conn.close()
    except RedisError:
        health_status["redis"] = False

    try:
        celery_status = celery_app.control.ping(timeout=3)
        health_status["celery"] = bool(celery_status)
    except (CeleryError, *_broker_errors()):
        health_status["celery"] = False

    response = Response(health_status)
    get_token(request)
    return response


@extend_schema(exclude=True)  # ty: ignore[invalid-argument-type]
@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
# No throttling — see health_check. For this endpoint it would also be
# a hidden shared dependency: the throttle's Redis GET runs before the
# view, so a Redis blip would fail the probe on every pod at once even
# though the view itself touches no backing service.
@throttle_classes([])
def health_live(request):
    """Lightweight probe — confirms the worker processes requests.

    Unlike ``health_check`` (which pings the database, Redis, and Celery
    and is suited to the *startup* gate), this touches no backing
    service, so a slow or unavailable dependency can never fail the
    Kubernetes readiness/liveness probes and de-register or restart
    healthy workers.
    """
    return Response({"status": "ok"})
