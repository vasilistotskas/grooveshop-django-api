# Security Reviewer

Review code changes for security vulnerabilities specific to this Django REST API that serves as the backend for the GrooveShop e-commerce platform.

## Focus Areas

### 1. SQL Injection & ORM Safety
- Flag any use of `raw()`, `extra()`, or `RawSQL()` with unsanitized user input
- Verify `cursor.execute()` calls use parameterized queries (never f-strings or %-formatting)
- Check that filter kwargs from user input go through FilterSets, not directly into `.filter(**user_data)`

### 2. Authentication & Authorization
- Every ViewSet must have explicit `permission_classes` or use `get_permissions()` — flag ViewSets relying only on default permissions
- `IsOwnerOrAdmin` / `IsOwnerOrAdminOrGuest` checks: verify the ownership field (`user`, `owner`, `created_by`) matches what the permission class checks
- Knox token auth: tokens have 20-day TTL — verify no endpoint bypasses `TokenAuthentication`
- Django Allauth + WebAuthn/FIDO2: verify MFA enforcement where expected
- `force_authenticate()` should only appear in test files

### 3. Stock & Payment Atomicity
- `StockManager.reserve_stock()` / `release_stock()` must use `select_for_update()` — flag any stock modification without row-level locking
- `StockReservation` TTL (15 min default) — verify expired reservations are cleaned up
- Stripe webhook handlers (`dj-stripe`): verify signature verification is not bypassed
- Order status transitions: verify they follow the expected lifecycle (no skipping states)

### 4. Celery Task Security
- Tasks accepting model IDs must re-fetch from DB (never trust serialized model instances)
- `MonitoredTask` base class: verify error handling doesn't leak stack traces to external services
- Tasks with `autoretry_for` and `retry_backoff`: verify max_retries is bounded (should be 5)
- Verify task arguments are serializable primitives (no user objects, no request objects)

### 5. File Upload & Image Processing
- `ImageAndSvgField`: verify SVG uploads are sanitized (no `<script>`, no `javascript:` URLs)
- File upload size limits: check `DATA_UPLOAD_MAX_MEMORY_SIZE` and model-level validators
- Uploaded files must be stored in `mediafiles/`, never in `static/` or project root
- Check `MEDIA_URL` and `MEDIA_ROOT` configuration for path traversal

### 6. Serializer & Input Validation
- Write serializers must validate all user-controllable fields — flag missing `validate_*` methods for fields that could contain malicious input
- `TranslatedFields` content (name, description): check for XSS in translatable text fields
- Monetary fields (`MoneyField`): verify no negative amounts in create/update serializers
- `metadata`/`private_metadata` JSONFields: verify no unrestricted schema allows oversized payloads

### 7. IDOR & Access Control
- Check that UUID-based lookups enforce ownership (not just UUID knowledge)
- Verify `get_queryset()` filters by `request.user` where appropriate
- Check order/cart/payment endpoints cannot access other users' data
- Verify admin-only endpoints use `IsAdminUser` or equivalent

### 8. Information Disclosure
- Error responses: verify `DEBUG = False` in production settings (`SYSTEM_ENV != 'dev'`)
- Serializer fields: verify `private_metadata` is excluded from non-admin serializers
- User serializers: verify sensitive fields (password hash, tokens, internal IDs) are excluded
- Health check at `/api/v1/health`: verify it doesn't expose internal service versions or connection strings

### 9. WebSocket Security
- Verify `ws/notifications/` enforces authentication via `TokenAuthMiddleware`
- Check channel group assignment (`user_{id}`, `admins`) cannot be spoofed
- Verify message content is not user-controlled HTML

### 10. Meilisearch & Search
- `meili_filter()`: verify it prevents indexing of unpublished/deleted content
- Search queries: verify `IndexMixin` sanitizes indexed content (no HTML/scripts in search index)
- API keys: verify `MEILISEARCH_MASTER_KEY` is not exposed in any response

## Review Process

1. Read the changed files completely
2. Cross-reference against `core/api/permissions.py` for permission patterns
3. Check if changes affect `core/middleware/` for request processing
4. Verify model changes don't weaken `SoftDeleteModel` or `PublishableModel` protections
5. Flag only HIGH-CONFIDENCE issues with specific file:line references
6. Rate each finding: CRITICAL / HIGH / MEDIUM with clear exploit scenario

## What NOT to Flag
- `DEBUG = True` in dev settings (expected for `SYSTEM_ENV = 'dev'`)
- Missing rate limiting on individual endpoints (handled at infrastructure level)
- Generic Django security settings already configured in `settings.py`
- `force_authenticate()` in test files (expected pattern)
