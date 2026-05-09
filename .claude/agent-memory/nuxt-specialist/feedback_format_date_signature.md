---
name: formatDate signature
description: app/utils/date.ts::formatDate takes a single string arg; locale is read internally — do not pass locale as second argument
type: feedback
---

`formatDate` in `app/utils/date.ts` has signature `formatDate(string: string): string`. It reads locale internally via `useNuxtApp().$i18n.locale`. The plan suggested `formatDate(date, locale.value)` but that second argument does not exist and would be silently ignored or cause a TypeScript error.

**Why:** The util was written to centralise locale access so callers don't need to thread locale through manually.

**How to apply:** When generating any component that formats dates, use `formatDate(someIsoString)` — never pass a second locale argument.
