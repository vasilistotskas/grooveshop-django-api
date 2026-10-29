# Email template preview

The admin tool that renders any transactional email the platform ships, in
any of the configured languages, against sample data or a real order.

**Nothing in this package sends email.** Sending lives with the domain that
owns the event — `order/notifications.py`, `order/tasks.py`, `b2b/tasks.py`,
`giftcard/tasks.py`, `product/tasks.py`, `tenant/billing.py`,
`shipping/alerts.py`, `shipping_acs/tasks.py`, `shipping_boxnow/tasks.py`,
`contact/tasks.py`, `user/tasks.py`, `core/tasks.py`. The order emails are
`EmailType`s declared in `order/emails.py` and sent by `emails.send`, and this
preview renders them through `emails.render.render`; the others still build
their own inside the task. This package is read-only tooling for humans.

Mounted at `admin/email-templates/` (locale-prefixed) on a **store** admin
host only; the views query `Order`, which lives in TENANT_APPS. Every view is
wrapped in `admin.site.admin_view` — `urls.py`'s module docstring explains why
`staff_member_required` is not enough here.

The merchant-facing guide to this page is published (both languages) at
`docs.grooveshop.space/admin/storefront/email-templates`; it is the one to
update when the page's behaviour changes.

## Files

| File | Role |
|---|---|
| `registry.py` | Discovers templates by walking `core/templates/emails/`. |
| `preview_service.py` | Renders one template against a context. |
| `sample_data.py` | Made-up orders/users/subscriptions for previews. |
| `config.py` | Optional per-template metadata for the preview list. |
| `admin_views.py`, `urls.py` | The page and its AJAX endpoints. |

## Discovery follows the filesystem, not the config

Every `.html` under `core/templates/emails/` except `base/` (the shared
layout) is treated as a sendable template and appears in the admin list. A
template needs **no** entry in `config.py` to be discovered, previewed, or
sent.

`config.py` only supplies nicer metadata for the templates listed in it: a
written description and an order-status association. Anything absent falls
back to its directory as the category, the description `"Email template"`,
and `is_used=True` (see `registry.py`).

This is the part that has broken before. Discovery used to iterate the three
*configured* categories while nine directories existed on disk, so whole
categories were invisible in the admin UI, and preview resolution inferred a
directory from the template *name*, which only works for `order_` and
`subscription_` prefixes. `tests/integration/core/email/test_registry_alignment.py`
now fails if any template on disk is not discovered.

## `config.py` is preview-only

`EmailTemplateConfig` is imported by `registry.py` and `preview_service.py`
and nowhere else. It holds no subject: a subject is built, and translated, by
the code that sends the mail. Adding a template to `config.py` changes this
admin page and nothing about the mail that goes out.

## What the preview shows

- **An order email, from one of the store's recent orders**: the email that
  order would be sent. The registered type that sends the template is
  rendered by `emails.render.render`, the function the pipeline calls, from
  the record `order.emails.preview_object` finds for the order, so the
  subject (in the chosen language) and the context are the real ones. An
  order with nothing the email is about (no refund for `order_refunded`, no
  receipt for `retail_document_issued`, no shipment for the carrier emails,
  no chargeback for `dispute_notification`) gets a message saying so.
- **Any email, from sample data**: a registered type renders its own
  `sample` (an unsaved object graph) with its real subject; every other
  template renders against made-up values (`sample_data.py`) and shows **no
  subject**, because no type owns one for it yet. They get one, and a real
  record to preview, as their domain moves onto the email-type catalogue.

## Adding a template

1. Put `<name>.html` (and a `<name>.txt` twin) in the right directory under
   `core/templates/emails/`. It is discoverable at the next process start.
2. Send it from the task that owns the event, with its own subject and
   context.
3. Optionally add a `TemplateConfig` entry so the admin list shows a real
   description and preview subject instead of the placeholder.

Autoescaping applies to `.txt` templates too: an admin-authored HTML field
must be rendered `{{ field_text|safe }}` from a pre-stripped value, never
escaped. See the root `CLAUDE.md` note on transactional email rendering.

### Adding a directory

A new directory is scanned on its own, but it is **only listed once it has a
heading**: add it to `EmailTemplateConfig.DIRECTORY_HEADINGS`. A directory
without one is logged as an error and left out (never filed under a guessed
title), and `test_every_template_directory_has_a_heading` fails. Which sample
data it previews with is chosen by directory in
`EmailTemplatePreviewService._get_sample_context`: order sample data for
`order`, `cart`, `shipping_acs` and `giftcard`, subscription data for
`subscription`, user data for everything else.

## Caching

`EmailTemplateRegistry` discovers once per process and caches on the class. A
template added by a release shows up after a restart, not immediately; tests
call `EmailTemplateRegistry.clear_cache()`.

## Tests

```bash
uv run pytest tests/unit/core/email tests/integration/core/email
```

| Test | Covers |
|---|---|
| `unit/…/test_registry.py` | Discovery and lookup. |
| `unit/…/test_sample_data.py` | Sample context generators. |
| `integration/…/test_registry_alignment.py` | Every template on disk is visible. |
| `integration/…/test_preview_service.py` | Rendering and category resolution. |
| `integration/…/test_preview_matches_send.py` | An order email's preview equals the email its task sends, and every order template has a builder entry. |
| `integration/…/test_admin_access_control.py` | Per-tenant staff gating. |
| `integration/…/test_email_theme.py` | Shared layout/theming. |
