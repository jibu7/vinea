---
applyTo: "frontend/**"
---
# Frontend rules
- Next.js 15 App Router, React 19, TypeScript `strict`, Tailwind 4 (CSS-first config in `globals.css`). Design tokens live in `src/styles/tokens.css` — never hard-code colors/spacing in components.
- Direction is **hybrid**: airy shell (dashboard, settings, onboarding) + **dense keyboard-first document workspaces** (invoice/GRV/JE): spreadsheet-grade line grid, sticky Exclusive/Tax/Inclusive totals footer, status chips, autosave drafts. Reuse the shared `DocumentForm` engine — never rebuild the header+lines+totals pattern per document type.
- Navigation has four intents: Maintenance / Transactions / Enquiries / Reports, filtered by permissions. Menu ordering follows `docs/` Appendix C.
- Data: TanStack Query for server state, Zustand only for session/UI state, React Hook Form + Zod for forms. API client in `src/lib/api.ts` uses `credentials: "include"` (httpOnly cookies) — never store tokens.
- Money is displayed via the `Money` component which respects the currency's `decimal_places` (RWF shows no decimals).
- Accessibility: keyboard navigation for every grid action; visible focus rings.
