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

## Coded modules

`CODED_MODULES` in the guard test lists the modules where every
`ValidationError` carries a code; a module is added when it is finished.
Done: product reviews, favourites and alerts; cart items and cart; user
addresses and account; blog comments; order items, orders and the order
views.

Follow-up (not coded yet): contact and feedback, newsletter and
subscriptions, search query validation, loyalty, B2B profile, tenant and
staff APIs, page-config and the other staff-only serializers.

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

Still different, listed so a client knows what not to expect: notification
views (`{"error": "<translated text>"}`, `{"error", "success"}`), the
settings API (`{"error": "missing_key"}`), the webhook receivers and
platform/staff APIs, subscription and category-image views.
