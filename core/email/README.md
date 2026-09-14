# Email template preview

The admin tool that renders any transactional email the platform ships, in
any of the configured languages, against sample data or a real order.

**Nothing in this package sends email.** Sending lives with the domain that
owns the event — `order/notifications.py`, `order/tasks.py`, `b2b/tasks.py`,
`giftcard/tasks.py`, `product/tasks.py`, `tenant/billing.py`,
`shipping/alerts.py`, `shipping_acs/tasks.py`, `shipping_boxnow/tasks.py`,
`contact/tasks.py`, `user/tasks.py`, `core/tasks.py` — each building its own
subject and context. This package is read-only tooling for humans.

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
written description, an order-status association, and a preview subject.
Anything absent falls back to its directory as the category, the description
`"Email template"`, and `is_used=True` (see `registry.py`).

This is the part that has broken before. Discovery used to iterate the three
*configured* categories while nine directories existed on disk, so whole
categories were invisible in the admin UI, and preview resolution inferred a
directory from the template *name*, which only works for `order_` and
`subscription_` prefixes. `tests/integration/core/email/test_registry_alignment.py`
now fails if any template on disk is not discovered.

## `config.py` is preview-only

`EmailTemplateConfig` is imported by `registry.py` and `preview_service.py`
and nowhere else. In particular, `subject_template` is the subject the
**preview** displays — never what a customer receives. Real subjects are
built and translated by the sending task. Adding a template to `config.py`
changes this admin page and nothing about the mail that goes out.

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

A new directory is picked up on its own and titled from its name
(`shipping_acs` → "Shipping Acs"). Add a `TemplateCategory` to `CATEGORIES`
only to give it a better display name or a different preview context
generator. Generators are registered in `preview_service`'s `generator_map`,
which today knows `generate_order_context`, `generate_subscription_context`
and `generate_user_context`, and falls back to order context.

## Preview subject placeholders

`subject_template` substitutes `{key}` and `{key[subkey]}` from the preview
context — `"Your Order #{order[id]} Has Shipped"`.

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
| `integration/…/test_admin_access_control.py` | Per-tenant staff gating. |
| `integration/…/test_email_theme.py` | Shared layout/theming. |
