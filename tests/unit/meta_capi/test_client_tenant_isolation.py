"""The Meta CAPI client must never post one tenant's events with
another tenant's credentials.

One Celery worker serves every tenant. The facebook_business SDK is
built around a PROCESS-GLOBAL default api (``FacebookAdsApi.init`` calls
``set_default_api``), and the old client relied on it: it cached the
init per (token, version) and let ``EventRequest.execute()`` read the
default at send time. On a cache HIT the default was never re-set, so a
dispatch for tenant A that followed tenant B's dispatch posted A's
events under B's token.

These tests pin the fix: each ``send`` resolves its OWN standalone api
from its own token and hands it to the pixel call explicitly, so the
credentials used are a pure function of the sending client — never of
whichever tenant last dispatched.
"""

from __future__ import annotations

import sys
import types
from unittest import mock

import pytest

from meta_capi import client as client_module
from meta_capi.client import MetaCapiClient


class _FakeSession:
    """Stand-in for FacebookSession — records the token it was built
    with so a test can read back which credentials an api carries."""

    def __init__(self, app_id=None, app_secret=None, access_token=None, **kw):
        self.access_token = access_token


class _FakeApi:
    """Stand-in for a FacebookAdsApi INSTANCE (never the default)."""

    def __init__(self, session, api_version=None, **kw):
        self.session = session
        self.api_version = api_version


class _FakeResponse(dict):
    """create_event returns a dict-like AbstractCrudObject."""


class _RecordingPixel:
    """Captures the ``api`` each pixel call is pinned to."""

    calls: list[dict] = []

    def __init__(self, fbid=None, api=None):
        self.fbid = fbid
        self.api = api

    def create_event(self, fields=None, params=None):
        _RecordingPixel.calls.append({"pixel_id": self.fbid, "api": self.api})
        return _FakeResponse(events_received=1, fbtrace_id="trace", messages=[])


class _FakeEventRequest:
    def __init__(self, **kwargs):
        self._kwargs = kwargs

    def get_params(self):
        return {}


@pytest.fixture
def sdk_stubs(monkeypatch):
    """Install fake facebook_business modules so the client's lazy
    imports resolve to recording stubs, and clear the api cache."""
    _RecordingPixel.calls = []
    client_module._api_cache.clear()

    def _install(path: str, **attrs):
        mod = types.ModuleType(path)
        for name, value in attrs.items():
            setattr(mod, name, value)
        monkeypatch.setitem(sys.modules, path, mod)
        return mod

    _install("facebook_business.api", FacebookAdsApi=_FakeApi)
    _install("facebook_business.session", FacebookSession=_FakeSession)
    _install("facebook_business.adobjects.adspixel", AdsPixel=_RecordingPixel)
    _install(
        "facebook_business.adobjects.serverside.event_request",
        EventRequest=_FakeEventRequest,
    )
    _install(
        "facebook_business.exceptions",
        FacebookRequestError=type("FacebookRequestError", (Exception,), {}),
    )
    yield


def _send(pixel_id: str, token: str) -> None:
    MetaCapiClient(
        pixel_id=pixel_id,
        access_token=token,
        api_version="v21.0",
    ).send([mock.Mock(name="event")])


def test_each_send_uses_its_own_token(sdk_stubs):
    _send("pxA", "tok-A")
    (call,) = _RecordingPixel.calls
    assert call["pixel_id"] == "pxA"
    assert call["api"].session.access_token == "tok-A"


def test_interleaved_tenants_do_not_bleed_credentials(sdk_stubs):
    """A then B then A — the A-after-B send is the regression: with the
    old default-api design it would carry B's token."""
    _send("pxA", "tok-A")
    _send("pxB", "tok-B")
    _send("pxA", "tok-A")

    tokens = [c["api"].session.access_token for c in _RecordingPixel.calls]
    pixels = [c["pixel_id"] for c in _RecordingPixel.calls]
    assert pixels == ["pxA", "pxB", "pxA"]
    assert tokens == ["tok-A", "tok-B", "tok-A"]


def test_api_instances_are_cached_per_token(sdk_stubs):
    """Same token reuses one api instance; a different token never does."""
    _send("pxA", "tok-A")
    _send("pxB", "tok-B")
    _send("pxA", "tok-A")

    api_first_a = _RecordingPixel.calls[0]["api"]
    api_b = _RecordingPixel.calls[1]["api"]
    api_second_a = _RecordingPixel.calls[2]["api"]
    assert api_first_a is api_second_a
    assert api_b is not api_first_a


def test_no_global_default_api_is_ever_set(sdk_stubs):
    """The fix must not call FacebookAdsApi.init()/set_default_api — the
    fake api has neither, so a regression to the default-api path would
    raise AttributeError here rather than pass silently."""
    _send("pxA", "tok-A")
    assert not hasattr(_FakeApi, "_default_api")
