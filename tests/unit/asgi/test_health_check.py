from asgiref.typing import HTTPResponseBodyEvent, HTTPResponseStartEvent

from asgi.health_check import health_check


async def test_health_check():
    async def app(scope, receive, send):
        await send(
            HTTPResponseStartEvent(
                type="http.response.start",
                status=200,
                headers=[(b"content-type", b"text/plain")],
                trailers=False,
            )
        )
        await send(
            HTTPResponseBodyEvent(
                type="http.response.body", body=b"app", more_body=False
            )
        )

    async def receive():
        raise NotImplementedError()

    async def send(event):
        events.append(event)

    scope = {"type": "http", "path": "/not-health"}
    events = []
    health_check_app = health_check(app, "/health")
    await health_check_app(scope, receive, send)
    assert events[1]["body"] == b"app"

    scope = {"type": "http", "path": "/health"}
    events = []
    health_check_app = health_check(app, "/health")
    await health_check_app(scope, receive, send)
    assert events[1]["body"] == b""
