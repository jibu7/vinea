# STOP-gate approvals

Phases carry mandatory STOP gates: the work pauses, a report goes to the owner, and nothing
continues until they say so. Those approvals happened in conversation and left no trace, which
is why P3's DoD clause "owner approved the step-2 prototypes" came back **unverifiable** in
`p3-dod-audit.md` — not because it did not happen, but because nothing recorded it.

One line per gate, from here on. An approval that is not written down cannot later be told
apart from one that was assumed.

| Phase | Step | Gate | Artefact approved | Commit | Approving message |
|---|---|---|---|---|---|
| P3 | 2 | Prototypes | `/design/prototypes/{dashboard,workspace,pos}` | not recorded | **Approved outside record, owner confirmed.** Backfilled at P4 step 7; the approval happened, the evidence was never committed. Closes the audit line as answered rather than open. |
| P4 | 2 | Posting contract and control-account guard | `partner_documents` schema, PostingEngine guard, `assert_subledger_invariants` | `ffffe52` | "Continue" — owner approved the step-2 report and the phase proceeded to documents. Recorded retrospectively at step 7 from this conversation; the message was not captured verbatim at the time. |
| P4 | 5 | Ageing, statements, listings, enquiries | ageing by bucket set, statement PDF jobs, transaction listings, partner enquiries | `ffffe52` | "Continue" — owner approved the step-5 report and the phase proceeded to UI. Recorded retrospectively at step 7; not captured verbatim. |
| P4 | 6 | Maintenance UI (review, not a plan gate) | eight AR/AP maintenance screens | `f65581e` | "Step 6 accepted with these closes" — accepted with seven follow-up items, all delivered. |

## What a good row looks like

Everything except the first three: **the artefact named, the commit it was at, and the owner's
words**. The three backfilled rows are weaker on purpose — they record what is actually known
rather than reconstructing a quote, and say so.

P4 steps 2 and 5 share a commit because both reports were approved before the phase's work was
first pushed; the commit is where that work landed, not where each gate was cleared. Future
gates should be recorded when they happen, against the commit under review at that moment.

## Filling this in

At each STOP gate, before continuing:

1. `git rev-parse --short HEAD` — the commit the report describes.
2. Paste the owner's approving message verbatim, however short.
3. Name the artefact concretely enough that someone can go and look at it.
