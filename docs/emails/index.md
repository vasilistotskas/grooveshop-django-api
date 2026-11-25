# Emails

The email pipeline: one catalogue of email types, one function that sends
them, and the switch and opt-out a store or a recipient uses to stop one.
Source: `src/emails/` (`types.py`, `declare.py`, `registry.py`, `gates.py`,
`recipients.py`, `sender.py`, `headers.py`, `optout.py`, `render.py`,
`send.py`, `transport.py`, `resend.py`, `log.py`, `status.py`, `tasks.py`,
`preview.py`, `figures.py`, `testsend.py`, `models.py`, `services.py`),
`tests/utils/emails.py` and
`tests/utils/golden.py` (the test helpers). The code wins over this page.

**Scope.** Every email the platform sends is on the pipeline: the order,
shipping, product, gift card, B2B, contact, account, cart, operational alert
and allauth (account, MFA and social sign-in) emails and the platform's own
mail (billing notices, the staff password reset, the health and backup
alerts); see Registered types in [`types.md`](types.md). The recorded golden
files keep each email's output stable (see Tests in [`delivery.md`](delivery.md)).

## Which file to read

This is one reference in several files, so each fits in one read. Read
the file for what you are changing. The headings keep their names.

| File | Read it before changing |
|---|---|
| [`types.md`](types.md) | what the pipeline is made of, how an email type is declared, the list of registered types (order, shipping, product, gift card, B2B, contact, cart, `ops.*`, `platform.*`, the newsletter form, email preferences) |
| [`sending.md`](sending.md) | `send_email`: attachments, language, sender, colours, the gates, the store's switch and its admin pages, the preview, the opt-out and the unsubscribe endpoint, `EMAIL_HASH_KEY`, headers, allauth mail |
| [`delivery.md`](delivery.md) | the transports and Resend, guarding the switch, failures, `EmailLog`, the Resend webhook and suppression, what is not built yet, the tests |

Keep each file well under 40,000 characters. A file that outgrows the
limit is split on a subject, and this table gains a row.
