---
name: Viva Wallet Webhook Security Design
description: Key security decisions and contracts in the Viva Wallet webhook handler
type: project
---

The Viva Wallet webhook at `order/views/viva_webhook.py` uses IP whitelisting (not HMAC) because Viva dashboard webhooks do not send signatures.

**Verification flow for event 1796 (Payment Created):**
1. IP whitelist check (`VIVA_WEBHOOK_IPS_PRODUCTION` / `VIVA_WEBHOOK_IPS_DEMO`)
2. StatusId == "F" check (skip if not "F")
3. TransactionId must be present (no ID = reject, save metadata as processed)
4. Viva Transaction API verification via `VivaWalletPaymentProvider.get_payment_status()`
5. If API call fails (None status): raise RuntimeError → 500 → Viva retries; event_key NOT persisted (transaction rolled back)
6. If API returns non-COMPLETED: skip, save metadata as processed (no retry)

**Why:** When our verification API call to Viva fails, we return 500 (not 200) so Viva retries. The event_key is intentionally NOT saved in that case so the retry gets a fresh run. All other skip paths DO save the event_key to prevent duplicate processing.

**IP extraction:** Takes rightmost XFF entry. Safe because Traefik (K3s default, no upstream XFF trust) replaces XFF with only the direct client IP — Viva's IP. Cannot be spoofed.

**URL:** `viva-wallet/webhook/` — top-level path, NOT under `api/v1/` or i18n prefix. This is intentional; Viva calls it from their side.

**How to apply:** When modifying the webhook, maintain the retry contract: only `raise RuntimeError` (rolls back event_key) for transient infrastructure failures; save metadata + return for permanent skip decisions.
