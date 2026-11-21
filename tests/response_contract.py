"""Every API response the suite receives is what the schema documents.

The storefront parses each body with Zod schemas generated from
``schema.yml`` and answers 422 to one they reject, so a serializer that
drifts from its declaration is a live failure, whichever test happens upon
it. ``django.test.Client.request`` (which ``APIClient`` goes through) is
wrapped for the whole session, and each response is judged against the
document ``manage.py spectacular`` writes (``tests.utils.openapi``):

- **The operation**: the request path matched against the document's
  paths. A path that fits several is given to the one with the fewest
  parameters (``media/resolve`` before ``media/{id}``); two with as few
  fail the request, since the document cannot say which one answered.
- **The response**: the operation's entry for the status code. When it
  documents a JSON body and the response is JSON, the body must satisfy
  it, or the request raises with the method, the path, the status and the
  first schema error. Error bodies are judged the same way (the error
  contract, ``docs/error-contract.md``).
- **Not judged, counted**: a status the operation does not document, a
  documented status without a JSON body, a body that is not JSON (a file,
  a CSV, a redirect), a method the path does not have (405) and an
  ``/api/v1`` path no operation fits. The terminal summary gives the
  counts; ``-v`` lists them by operation, so the gap stays visible.
  Under ``STRICT_PREFIXES`` an undocumented status fails as well.
- **A known drift**: ``@pytest.mark.known_schema_drift(method, path,
  status, issue=N)`` on a test lets a body of that operation and status
  fail, while its issue is open. A test so marked that receives no such
  failing body fails: the drift is gone, take the marker off.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from functools import cache
from typing import Any

import pytest
from django.test import Client
from jsonschema.exceptions import best_match

from tests.utils.openapi import judge, openapi_document

#: The prefix every documented operation lives under.
API_PREFIX = "/api/v1/"

#: Operations under these prefixes declare every status they answer, except
#: ``ANSWERED_BEFORE_THE_VIEW``.
STRICT_PREFIXES = ("/api/v1/builder/",)

#: Answered by authentication before any view: no operation declares them.
ANSWERED_BEFORE_THE_VIEW = frozenset({401, 403})

_PARAMETER = re.compile(r"\{[^}]+\}")


@dataclass
class Tally:
    """What this process judged, and what it could not."""

    validated: int = 0
    not_judged: Counter[str] = field(default_factory=Counter)

    def merge(self, other: dict[str, Any]) -> None:
        self.validated += other["validated"]
        self.not_judged.update(other["not_judged"])

    def as_dict(self) -> dict[str, Any]:
        return {
            "validated": self.validated,
            "not_judged": dict(self.not_judged),
        }


@dataclass
class KnownDrifts:
    """The running test, and which of its ``known_schema_drift`` markers a
    failing body has met. The markers are read when asked for, so one a
    test applies itself (``request.applymarker``, for one parameter of
    several) counts too."""

    node: pytest.Item | None = None
    met: set[tuple[str, str, int]] = field(default_factory=set)

    @property
    def allowed(self) -> dict[tuple[str, str, int], int]:
        if self.node is None:
            return {}
        return {
            (
                marker.args[0].upper(),
                marker.args[1],
                int(marker.args[2]),
            ): marker.kwargs["issue"]
            for marker in self.node.iter_markers("known_schema_drift")
        }


TALLY = Tally()
DRIFTS = KnownDrifts()


@cache
def _templates() -> tuple[tuple[re.Pattern[str], str, int], ...]:
    return tuple(
        (
            re.compile("^" + _PARAMETER.sub("[^/]+", template) + "$"),
            template,
            template.count("{"),
        )
        for template in openapi_document()["paths"]
    )


def operation_path(path: str) -> str | None:
    """The documented path ``path`` is an instance of, if any."""
    fits = sorted(
        (parameters, template)
        for pattern, template, parameters in _templates()
        if pattern.match(path)
    )
    if not fits:
        return None
    if len(fits) > 1 and fits[0][0] == fits[1][0]:
        raise AssertionError(
            f"{path} fits {fits[0][1]} and {fits[1][1]} equally: the "
            "schema cannot say which operation answered"
        )
    return fits[0][1]


def _is_json(response) -> bool:
    return not response.streaming and response.get(
        "Content-Type", ""
    ).startswith("application/json")


def check(method: str, path: str, response) -> None:
    """Judge ``response`` to ``method path`` against the document."""
    if not path.startswith(API_PREFIX):
        return
    template = operation_path(path)
    status = response.status_code
    if template is None:
        TALLY.not_judged[f"no operation fits {method} {path}"] += 1
        return
    operation = openapi_document()["paths"][template].get(method.lower())
    if operation is None:
        TALLY.not_judged[f"{method} {template}: no such method"] += 1
        return
    declared = operation["responses"].get(str(status))
    if declared is None:
        if (
            template.startswith(STRICT_PREFIXES)
            and status not in ANSWERED_BEFORE_THE_VIEW
        ):
            raise AssertionError(
                f"{method} {template} answered {status} "
                f"({response.content[:500]!r}), which it does not declare"
            )
        TALLY.not_judged[f"{method} {template} {status}: undocumented"] += 1
        return
    schema = (
        declared.get("content", {}).get("application/json", {}).get("schema")
    )
    if schema is None or not _is_json(response):
        TALLY.not_judged[f"{method} {template} {status}: no JSON body"] += 1
        return
    TALLY.validated += 1
    error = best_match(
        judge(openapi_document())
        .validator(schema)
        .iter_errors(json.loads(response.content))
    )
    if error is None:
        return
    key = (method, template, status)
    if key in DRIFTS.allowed:
        DRIFTS.met.add(key)
        return
    raise AssertionError(
        f"{method} {template} answered {status} with a body its schema "
        f"rejects, at {error.json_path}: {error.message}"
    )


_original_request = Client.request


def _checked_request(self, **request):
    response = _original_request(self, **request)
    sent = response.wsgi_request
    check(sent.method, sent.path_info, response)
    return response


@pytest.fixture(scope="session", autouse=True)
def responses_match_the_schema():
    """Judge every test client response for the whole session.

    The document is built here, before any test runs, and not on the
    first response: a test that overrides ``ROOT_URLCONF`` (the platform
    admin's ``tenant.urls_public``) would otherwise build it from that URL
    conf, and every later test would be judged against a document missing
    the store's operations.
    """
    openapi_document()
    Client.request = _checked_request
    yield
    Client.request = _original_request


@pytest.fixture(autouse=True)
def known_schema_drifts(request):
    """Allow the drifts the test's ``known_schema_drift`` markers name, and
    fail a marker whose drift no longer shows."""
    DRIFTS.node, DRIFTS.met = request.node, set()
    yield
    gone = unmet_drifts()
    DRIFTS.node, DRIFTS.met = None, set()
    if gone:
        pytest.fail(
            "No body broke the schema for "
            + ", ".join(gone)
            + ": the drift is gone, remove its known_schema_drift marker"
        )


def unmet_drifts() -> list[str]:
    """The current test's markers that no failing body has met."""
    allowed = DRIFTS.allowed
    return [
        f"{method} {path} {status} (issue {allowed[method, path, status]})"
        for method, path, status in sorted(set(allowed) - DRIFTS.met)
    ]


def pytest_sessionfinish(session):
    workeroutput = getattr(session.config, "workeroutput", None)
    if workeroutput is not None:
        workeroutput["response_contract"] = TALLY.as_dict()


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node, error):
    output = getattr(node, "workeroutput", None) or {}
    if "response_contract" in output:
        TALLY.merge(output["response_contract"])


def pytest_terminal_summary(terminalreporter, config):
    if hasattr(config, "workeroutput"):
        return
    if not TALLY.validated and not TALLY.not_judged:
        return
    terminalreporter.write_line(
        f"API responses judged against the schema: {TALLY.validated}; "
        f"not judged: {TALLY.not_judged.total()} "
        "(-v lists them)"
    )
    if config.get_verbosity() > 0:
        for reason, count in sorted(TALLY.not_judged.items()):
            terminalreporter.write_line(f"  {count:>5}  {reason}")
