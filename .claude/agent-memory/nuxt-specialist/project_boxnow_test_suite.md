---
name: BoxNow Frontend Test Suite
description: Location and structure of the BoxNow Nuxt test files written in Wave 4
type: project
---

8 test files written for the BoxNow shipping integration (Wave 4). All source components and composables exist.

**Why:** BoxNow integration adds new composables, 5 checkout components, and 3 order components — all need test coverage before Wave 5 verification.

**How to apply:** When asked about BoxNow tests, reference these files. When adding more BoxNow components, follow the same patterns.

## File list

| File | Project | Tests |
|---|---|---|
| `test/unit/composables/useBoxNowWidget.spec.ts` | unit (node) | 41 |
| `test/nuxt/composables/useBoxNowParcelState.spec.ts` | nuxt | 13 (incl. `it.each` over all 12 states) |
| `test/nuxt/components/Checkout/StepShipping.spec.ts` | nuxt | 9 |
| `test/nuxt/components/Checkout/BoxNowLockerPicker.spec.ts` | nuxt | 9 |
| `test/nuxt/components/Checkout/SelectedBoxNowLocker.spec.ts` | nuxt | 8 |
| `test/nuxt/components/Order/BoxNowStateBadge.spec.ts` | nuxt | 4 (+ `it.each` over 12 states) |
| `test/nuxt/components/Order/BoxNowEventTimeline.spec.ts` | nuxt | 9 |
| `test/nuxt/components/Order/BoxNowTracking.spec.ts` | nuxt | 11 |
| `test/nuxt/integration/checkout-boxnow-flow.spec.ts` | nuxt | 5 (1 `describe.skip`) |

## Skipped tests

- `describe.skip('Full checkout page (blocked)', ...)` in the integration file: mounting the full checkout page requires mocking ~10 composables and 5 API endpoints. Marked as TODO once a shared checkout page test helper is available.

## Key decisions

- `useBoxNowParcelState` test is in `test/nuxt/` (not `test/unit/`) because it calls `useNuxtApp().$i18n`.
- `useBoxNowWidget` test is in `test/unit/` (node env) — all exports are pure functions.
- Translated label assertions use `expect.any(String)` per project convention.
- `mockNuxtImport('useRuntimeConfig', ...)` stubs `boxnowPartnerId` in all picker-related tests.
- `mockNuxtImport('useBoxNowParcelState', ...)` in `BoxNowTracking.spec.ts` gives predictable presentation objects.
- postMessage simulation uses `window.dispatchEvent(new MessageEvent('message', { origin, data }))`.
