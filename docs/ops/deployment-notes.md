# Deployment notes — things a first deploy must know, collected as they arise

**Who this is for:** whoever writes and runs the go-live runbook. The Master Plan puts
production infra, runbooks and launch hardening in **P12**; v4's Phase 13 was dissolved in the
v5 delta log, so if you came here looking for "P13's deployment notes", this is that register.

**What belongs here:** one line per fact that is invisible in a diff and only matters at deploy
time — a behaviour change against data written by the previous version, a first-run cost, an
ordering constraint between a migration and a restart. Not a change log: if a reviewer of the
phase report would have seen it, it does not need a line. Each entry names the phase and step
that created it, the commit, and the document that explains it. Newest first.

| phase | commit | what a deploy needs to know |
|---|---|---|
| P7 step 3 | `e99b191` | **Two persisted digests change, and no back-fill migration is coming for either.** One canonicaliser replaced two, so every stored `fiscal_items.last_payload_hash` and every `idempotency_hash` on the twelve document tables was written by a function that no longer exists. Unconditionally — the serialisation changed, so it is not only prices with trailing zeros. Consequences: each item re-registers **once**, on whatever next touches it (`/items/saveItems` is an upsert keyed on `itemCd`, and re-registration is the mechanism a rename already uses, so this is correct behaviour rather than a repair); and an `Idempotency-Key` issued before the deploy and retried after it answers `409 idempotency_key_reused` instead of replaying. That comparison **fails closed** — a stale stored hash can never turn a retry into a second posting. Full reasoning and the measured digests: `docs/p7-step-3-report.md`, "Deploy-time knock-ons of `e99b191`". |
