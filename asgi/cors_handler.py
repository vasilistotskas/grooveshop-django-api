from fnmatch import fnmatchcase

from asgiref.typing import (
    ASGI3Application,
    ASGIReceiveCallable,
    ASGISendCallable,
    ASGISendEvent,
    HTTPResponseBodyEvent,
    HTTPResponseStartEvent,
    Scope,
)
from django.conf import settings


def cors_handler(application: ASGI3Application):
    async def cors_wrapper(
        scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable
    ):
        if scope["type"] != "http":
            await application(scope, receive, send)
            return
        request_origin: str = ""
        for header, value in scope.get("headers", []):
            if header == b"origin":
                request_origin = value.decode("latin1")
        origin_match = False
        if request_origin:
            for allowed_origin in settings.CORS_ALLOWED_ORIGINS:
                if fnmatchcase(request_origin, allowed_origin):
                    origin_match = True
                    break
        if scope["method"] == "OPTIONS":
            response_headers: list[tuple[bytes, bytes]] = [
                (b"access-control-allow-credentials", b"true"),
                (
                    b"access-control-allow-headers",
                    b"Origin, Content-Type, Accept, Authorization, "
                    b"Authorization-Bearer",
                ),
                (b"access-control-allow-methods", b"POST, OPTIONS"),
                (b"access-control-max-age", b"600"),
                (b"vary", b"Origin"),
            ]
            if origin_match:
                response_headers.append(
                    (
                        b"access-control-allow-origin",
                        request_origin.encode("latin1"),
                    )
                )
            await send(
                HTTPResponseStartEvent(
                    type="http.response.start",
                    status=200 if origin_match else 400,
                    headers=sorted(response_headers),
                    trailers=False,
                )
            )
            await send(
                HTTPResponseBodyEvent(
                    type="http.response.body", body=b"", more_body=False
                )
            )
        else:

            async def send_with_origin(message: ASGISendEvent):
                if message["type"] == "http.response.start":
                    res_headers = [
                        (key, val)
                        for key, val in message["headers"]
                        if key.lower()
                        not in {
                            b"access-control-allow-credentials",
                            b"access-control-allow-origin",
                            b"vary",
                        }
                    ]
                    res_headers.append(
                        (b"access-control-allow-credentials", b"true")
                    )
                    vary_header = next(
                        (
                            val
                            for key, val in message["headers"]
                            if key.lower() == b"vary"
                        ),
                        b"",
                    )
                    if origin_match:
                        res_headers.append(
                            (
                                b"access-control-allow-origin",
                                request_origin.encode("latin1"),
                            )
                        )
                        if b"Origin" not in vary_header:
                            if vary_header:
                                vary_header += b", Origin"
                            else:
                                vary_header = b"Origin"
                    if vary_header:
                        res_headers.append((b"vary", vary_header))
                    message["headers"] = sorted(res_headers)
                await send(message)

            await application(scope, receive, send_with_origin)

    return cors_wrapper
