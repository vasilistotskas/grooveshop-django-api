# Encryption of merchant secrets at rest

A store's third-party credentials live on its `tenant.Tenant` row, in the
public schema. They are stored as Fernet tokens (`cryptography`:
AES-128-CBC with an HMAC-SHA256 tag), so a database dump, replica or backup
that leaves the cluster carries ciphertext only. The application reads and
writes plain strings: `EncryptedCharField` (`core/db/encrypted_fields.py`)
encrypts on write and decrypts when a row is loaded.

## What is encrypted

Every `EncryptedCharField` on `Tenant`: `stripe_secret_key`,
`cloudflare_api_token`, `meta_capi_access_token`, `viva_wallet_api_key`,
`viva_wallet_client_secret`, `viva_wallet_webhook_verification_key`,
`acs_api_key`, `acs_company_password`, `acs_user_password`,
`box_now_client_secret`, `box_now_webhook_secret`, `acp_bearer_token`,
`chat_api_key`, `mydata_subscription_key`. Each maps onto a
`<name>_encrypted` text column. `mydata_user_id` is the TAXISnet username,
an identifier like `acs_user_id` and `viva_wallet_client_id`, and stays
plaintext.

The fields are excluded from `HistoricalTenant`, so no snapshot table holds
them either.

## The key

`FIELD_ENCRYPTION_KEYS` is a comma-separated list of Fernet keys, read in
`settings.py`. The first key encrypts; every key decrypts. It is never
derived from `SECRET_KEY`, so rotating one does not touch the other.

- Production and staging refuse to start without it, and refuse an entry
  that is not a valid Fernet key. Dev and CI fall back to a fixed,
  insecure key so a checkout boots; data written under it is not protected.
- `tests/settings.py` pins a fixed key.
- Generate a key with
  `uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
- **Losing every key loses every secret.** Keep the key in the
  `backend-secrets` SealedSecret and in the operator's own secret store;
  a database backup is useless without it. Merchants can re-enter their
  credentials, but only after the operator tells them to.
- The key must be present in every namespace and for the PreSync hook
  **before** the release that adds the encrypted columns deploys: the
  migration encrypts the existing plaintext and the app decrypts it.

## Behaviour to know

- The empty string stays empty and is never encrypted, so `if
  tenant.stripe_secret_key` and `exclude(field="")` still work.
- Fernet is randomised: one plaintext gives a different token each time.
  A lookup, index or `unique` constraint on a non-empty value can never
  match. Filter on a plain column and compare in Python (the edge purge in
  `core/cache/edge.py` does).
- A token no configured key opens raises `DecryptionError` when the row is
  loaded. That is deliberate: a store never silently runs with a
  ciphertext as its credential. If the key was lost or replaced, the
  tenant row cannot be loaded until the right key is back.
- Every save of a `Tenant` re-encrypts all its secrets (a different token,
  the same plaintext). Measured on the dev machine: decrypting one token
  takes about 5 microseconds, a fully populated row about 70 microseconds.
- `dumpdata` and the admin export the decrypted value, like any read
  through the model.

## Rotating the key

1. Generate a new key and put it **first** in `FIELD_ENCRYPTION_KEYS`,
   keeping the old one: `FIELD_ENCRYPTION_KEYS=<new>,<old>`. Update the
   `backend-secrets` SealedSecret in every namespace.
2. Deploy, so every backend and Celery pod runs with both keys. New writes
   use `<new>`; old tokens still decrypt.
3. Run `uv run python manage.py rotate_tenant_secrets` on one backend pod.
   It re-encrypts every token an older key wrote and prints only counts
   (`Re-encrypted N value(s) across M tenant(s); K already under the
   primary key.`). It is idempotent, and it aborts naming the tenant and
   field, writing nothing for them, if a token no configured key opens.
4. Remove `<old>` from `FIELD_ENCRYPTION_KEYS` and deploy again.

Do not skip step 2: a pod that still has only `<old>` cannot read a token
written under `<new>`.

## Not covered

Secrets stored elsewhere are outside this field: dj-stripe's `APIKey` and
`WebhookEndpoint` rows, allauth's `SocialApp`/`SocialToken`, and whatever a
tenant export (`dumpdata`) writes to disk.
