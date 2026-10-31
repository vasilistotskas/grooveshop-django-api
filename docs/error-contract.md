# API error contract

What an API error looks like on the wire, and how to raise one. Source:
`core/api/exception_handler.py` (the handler),
`REST_FRAMEWORK["EXCEPTION_HANDLER"]` in `settings.py`, and the guards in
`tests/unit/core/api/test_error_code_guards.py`. The code wins over this
page.

## The body

Every error keeps the body DRF gave it and gains two keys (camelCase on the
wire, like every other body):

| Error | Body |
|---|---|
| Plain (`NotFound`, `PermissionDenied`, `Throttled`, any `APIException`) | `{"detail": "...", "code": "not_found"}` |
| Validation (every `ValidationError` given a dict, including `{"detail": ...}`) | the keys as before, plus `"code"` and `"fields"` |

`fields` repeats the field keys, each a list of `{message, code}`, nested
where the serializer nests:

```json
{
  "firstName": ["This field may not be blank."],
  "code": "blank",
  "fields": {
    "firstName": [{"message": "This field may not be blank.", "code": "blank"}]
  }
}
```

- `code` of a validation error is the one code all its errors share, else
  `invalid`. When the body has a field named `code` (a coupon code), the
  top-level key is left to that field and `fields` is the only carrier.
- A nested serializer's field holds the nested object. A `many=True` child
  is keyed by the row's position (`"items": {"1": {"quantity": [...]}}`).
- A `5xx` `APIException` gains `code` only. Anything that is not an
  `APIException` is left to Django (a logged 500), so no exception text
  reaches a client through the handler.
- A bare list body (`ValidationError("...")` raised outside a serializer)
  cannot grow keys without changing type, so it is left as it was. Raise a
  dict instead (`ValidationError({"detail": ...}, code=...)`).
- The schema publishes the shapes as `ApiError`, `ApiValidationError` and
  `ApiFieldError`; plain-error response serializers carry an optional
  `code`.
- A 400 is documented as `ApiValidationError` or a plain body (`anyOf`),
  never as the plain body alone: a validation body has no `detail`.
  `core.api.schema.publish_error_schemas` adds the validation variant to every
  400 whose view declares a plain body (`PLAIN_400_BODIES`: `ErrorResponse`
  becomes `ApiError`; `PaymentProviderError` is listed beside `ApiError`), so a view keeps
  declaring `ErrorResponseSerializer` and a view with its own 400 body
  (`B2BErrorResponse`, `CouponErrorResponse`) is left alone.
- `ErrorResponse` is `{detail, code}` only. The one plain body that carries
  more is `PaymentProviderError` (`error`: the provider call's result), sent
  by the four order actions that call the payment provider.
- `tests/unit/core/api/test_error_schema.py` checks real 400, 401, 403, 404,
  409 and 429 bodies against what their operation documents (the `documented`
  fixture in `tests/conftest.py`). No operation documents a 405 or 415; the
  handler sends the same `{detail, code}` for them.

## Raising one

```python
raise ValidationError(
    {"product": _("You must have purchased this product to review it.")},
    code="must_have_purchased",
)
```

- **A code is an identifier, never a message.** Pass it as `code=`, never
  through `_()`. The sentence goes through `_()`, with the English sentence
  as the msgid and the Greek in `locale/el`. `must_have_purchased` used to
  be a msgid: the wire carried the Greek translation, and the storefront,
  comparing text, failed in Greek. `test_no_error_code_goes_through_gettext`
  fails on any `_("snake_case")` inside an API exception.
- Snake_case, named for what is wrong, stable once shipped (clients branch
  on it). Reuse a code across call sites when the meaning is the same
  (`insufficient_stock`, `quantity_not_positive`).
- A `dict` given with one `code=` gives that code to every value in it. A
  rule that yields several kinds of error builds each message as an
  `ErrorDetail(message, code)` (see `core/validators/address.py`).
- `NotFound`, `PermissionDenied` and `NotAuthenticated` already carry
  `not_found`, `permission_denied`, `not_authenticated`; give a specific
  code when a client tells the cases apart.

## What is coded

Every DRF `ValidationError` carries a `code=` (`test_every_drf_validation_error_has_a_code`
scans the whole project; Django's own `ValidationError` of models and admin
forms is staff-facing and not judged). A raise of a bare name, an error dict
a helper built, is trusted to code its own messages (see
`core/validators/address.py`).

Not coded, on purpose: `NotFound`, `PermissionDenied`, `NotAuthenticated`
keep their DRF code unless a client tells the cases apart, and the feature
gate stays a bare `not_found` so a switched-off route is indistinguishable
from a missing one.

## Responses a view builds itself

`return Response({...}, status=4xx)` never reaches the handler (DRF only
calls it for raised exceptions), so those keep their own shape. Where a
stable discriminator already existed, `code` now repeats it:

- `{"detail", "reason"}` (cart coupons and payment intents, gift cards,
  business profile): `code` equals `reason`.
- `{"detail", "error": {"type"}}` (order create and cancel): `code` equals
  `error.type`.
- Plain `{"detail"}` responses of the cart, order, account and alert views
  carry a `code` too.

Every other literal-dict error `Response` carries a `code` too
(`test_every_error_response_a_view_builds_has_a_code`); a body that had only
`{"error": "..."}` (notification views, product category images, the settings
API, the editor upload) gained `detail` and `code` beside it. Exempt, because a
provider or another protocol defines their body: the Viva and BoxNow webhook
receivers and the allauth rate-limit response (allauth's own `errors[].code`).
Bodies built from a variable (a serializer's errors, `error_data`) are not
judged; the blog reorder and comment actions now raise instead, so the handler
adds `code` and `fields`.

## Which codes are returned (monitoring)

Every request writes one access line (`core/middleware/access_log.py`), and
an error response's line names its `code`. It is read from the response,
not in the handler, because the handler sees only raised errors and the
order, cart and payment views build most of theirs themselves; the access
line sees both, once each. In production it is one JSON object (the
`JsonFormatter` in `core/logging.py`), which VictoriaLogs stores under
`log.*`:

```json
{"level": "INFO", "logger": "core.middleware.access_log",
 "schema": "webside", "correlation_id": "…",
 "message": "POST api/v1/order/<int:pk>/create_payment_intent 400 812.4ms payment_intent_failed",
 "method": "POST", "route": "api/v1/order/<int:pk>/create_payment_intent",
 "status": 400, "duration_ms": 812.4, "code": "payment_intent_failed"}
```

- `route` is the URL pattern, never the path; `schema` is the store. No
  message text, body or query string is logged, and the one header that
  is, `correlation_id`, is the only client-controlled value in the line.
  It is the client's `X-Correlation-ID` when one is sent (a UUID the
  storefront mints otherwise), bounded by `CorrelationIdMiddleware`: C0
  control characters and DEL are removed and the result is cut to 64
  characters. Nothing else is checked, so it can hold any other
  character, personal data included; the JSON formatter escapes it, so it
  cannot add a field or a line, and it must be treated as untrusted when
  read back (never as a key, a path or a template).
- `level` is `WARNING` for a 5xx and `INFO` otherwise. `django.request`
  still logs every 5xx at `ERROR` (with the traceback when an exception
  caused it); the access line never repeats the traceback.
- `code` is present only on a 4xx/5xx DRF response whose body has a string
  `code`. A Django error page (an unhandled exception's 500, an unknown
  host's 404) and a body whose `code` key is a field (a coupon code) have
  none.
- The log alerts in grooveshop-infrastructure
  (`manifests/app-constructs/victoria-metrics/logs-alerting/vmrule-grooveshop-logs.yaml`)
  count `log.code` per route, so **renaming a code silently drops it from
  its alert**: change the rule in the same change set. Query by hand with
  `_time:1h kubernetes.pod_namespace:="grooveshop" log.code:*
  | stats by (log.code, log.route, log.status) count()`.
