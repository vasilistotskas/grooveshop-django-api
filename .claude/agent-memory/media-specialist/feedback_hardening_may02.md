---
name: Media-stream hardening fixes (2026-05-02)
description: Four security/correctness fixes applied to grooveshop-media-stream
type: project
---

Four hardening fixes applied to grooveshop-media-stream. All validated by pnpm lint + pnpm run build (182 files, 0 errors).

**Why:** defense-in-depth pass requested by user to close unauthenticated admin endpoints, a prototype-loss bug, a temp file leak on Sharp errors, and partial-write races in the background processor.

**How to apply:** reference these patterns when adding new admin endpoints or new image processing paths.

## Fix 1 — Auth-gate /metrics and POST /health/circuit-breaker/reset

New guard: `src/MediaStream/common/guards/internal-secret.guard.ts`
- Uses `ConfigService` from `@nestjs/config` (NestJS native, not the custom wrapper) so it works from any module
- Reads `INTERNAL_ADMIN_SECRET` env var directly; fail-closed if unset (throws 401)
- Header: `x-internal-secret`

Config plumbing:
- `APP_CONFIG_SCHEMA` in `config-schema.util.ts`: added `internal.adminSecret` entry mapping to `INTERNAL_ADMIN_SECRET`
- `AppConfig` interface + `ensureConfigStructure` updated with `internal: InternalConfig`
- `.env.example` documents the new var

Applied to:
- `MetricsController` — class-level `@UseGuards(InternalSecretGuard)`
- `HealthController.resetCircuitBreaker()` — method-level `@UseGuards(InternalSecretGuard)`, new `POST /health/circuit-breaker/reset` endpoint (was documented in CLAUDE.md but missing from code)

Modules: guard registered as provider in `MetricsModule` and `HealthModule` (NestConfigModule is global so no extra import needed)

Rate-limit guard: removed blanket `/metrics` exemption; `/health/circuit-breaker/reset` POST also no longer exempt. K8s probes (`/health/*` GET) remain exempt.

## Fix 2 — sanitize() prototype preservation

File: `cache-image-resource.operation.ts` line ~238
Changed from `sanitize(req) as CacheImageRequest` to:
```typescript
const sanitizedPlain = await this.inputSanitizationService.sanitize(cacheImageRequest)
const sanitizedRequest = Object.assign(new CacheImageRequest(), sanitizedPlain)
```
`CacheImageRequest` uses `Object.assign(this, data)` in its constructor so this is safe.

## Fix 3 — .rst temp file try/finally cleanup

File: `cache-image-resource.operation.ts` — `processImageSynchronously()`
Wrapped the entire Sharp processing block (SVG detection + processing + write + rename) in try/finally.
`unlink(resourceTempPath).catch(() => {})` in the finally handles both the success and error paths.
The old standalone try/catch unlink was removed (subsumed by finally).

## Fix 4 — Atomic writes in background image processor

File: `image-processing.processor.ts` lines ~124-127
Added `rename` import from `node:fs/promises`.
Changed direct `writeFile(resourcePath, ...)` to temp+rename pattern:
```typescript
const tmpResource = `${resourcePath}.tmp`
const tmpMetadata = `${metadataPath}.tmp`
await Promise.all([writeFile(tmpResource, ...), writeFile(tmpMetadata, ...)])
await Promise.all([rename(tmpResource, resourcePath), rename(tmpMetadata, metadataPath)])
```
Mirrors the same pattern already used in the synchronous pipeline.
