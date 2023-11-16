import pytest
from asgiref.typing import HTTPResponseBodyEvent
from asgiref.typing import HTTPResponseStartEvent

from asgi.health_check import health_check


@pytest.mark.asyncio
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

    async def receive() -> None:
        raise NotImplementedError()

    async def send(event) -> None:
        events.append(event)

    # Test when path is not equal to health_url
    scope = {"type": "http", "path": "/not-health"}
    events = []
    health_check_app = health_check(app, "/health")
    await health_check_app(scope, receive, send)
    assert events[1]["body"] == b"app"

    # Test when path is equal to health_url
    scope = {"type": "http", "path": "/health"}
    events = []
    health_check_app = health_check(app, "/health")
    await health_check_app(scope, receive, send)
    assert events[1]["body"] == b""
