# P7 step 4 — where this branch was left off

Working note, not a report. Delete it when `docs/p7-step-4-report.md` lands; that document is
what step 4 owes, and nothing here belongs in it verbatim.

Branch `claude/p7-step-4`, last commit `f135983`, cut from `main` at `4573263` (PR #51, step 3,
merged). Tree clean, nothing unpushed.

## Done and verified

`ruff check .` clean; `pytest tests/tax/ -q` → 2 passed. Nothing else in step 4 has been run.

* `app/tax/vat.py` — decision 12's query: per-code base and tax off `journal_lines`, the
  sections, and the per-account tie with untagged movements listed line by line.
* `app/kernel/events.py` — `VatReturnPosted` added; `FxRevalued` promoted out of the stub block
  with the retirement note left where the stub stood. ADR-05's last kernel-side stub.
* `app/kernel/sequences.py` — the `vat_returns` claimant step 1 defined and never registered is
  registered, narrowed to `journal_entry_id IS NULL` (see decision **e** below).
* `tests/tax/` — the month with hand-worked literals, and the untagged-payment case.

## Written but never executed — treat as unverified

`file_return`, `reverse_return`, `_settlement_lines`, `_replay`, `_refuse_an_overlap`, and the
whole late-entry path (`_late`, `CodeTotal.late_*`, `AccountTie.late_total`). The code is there
and lints; no test has touched it. Write those tests before trusting any of it.

## Not started

Annex CSVs (sales and purchases) · decision 11's X/Z computation and the Z close · decision 13's
FX revaluation (preview, post with the mirror, reversal, the three refusals, the `fx_revaluation`
job kind) · endpoints, schemas and their `NO_UI` register lines (`GAP (P7, step 7)` for the
return and FX screens, `GAP (P7, step 8)` for Close day) · the step-4 report.

## Decisions taken so far, for the report's "Decisions worth review"

a. **A late entry is declared on the current return, not merely listed.** The filed period is
   closed, so a return that only listed it would leave that tax declared to nobody. It counts in
   the figures, appears in the late-entry list with its own date, and the tie carries its total
   separately so the difference against *this* range's account movement still adds up.
b. **The tie is per VAT account, aggregating every code that account is the tax account of.**
   The first run caught this: the Rwanda seed puts `VAT-IN-18` and `VAT-IN-IMP` both on `1400`,
   and a tie keyed one-code-per-account reported the genuine input VAT as untagged.
c. **A settlement line is told from a real tax line by its module.** Both sit on a VAT account
   carrying a tax code with `tax_amount = 0`, so no amount distinguishes them; `_line_query`
   excludes `module = 'tax'` from the declared side and `_untagged` lists it.
d. **The imports section is keyed on the seeded code string** (`IMPORT_TAX_CODE`). Everything
   else in the module keys on `nature`, which is neutral; the schema that would carry this
   properly was step 1's to add and it did not.
e. **A nil return posts no settlement entry** and therefore holds its own number, which is why
   the claimant exists. Without it the gapless check reads a nil return as a hole in the `VAT`
   run. Same shape as P5's valueless stock document.
f. **FX revaluation is planned for `app/subledger/revaluation.py`** — not written yet. The
   kernel imports no subledger models and revaluation reads partner open items, so it cannot
   live in `app/kernel/`; it sits beside `allocations.py`, which owns realized FX. Its posting
   module stays `gl` as decision 13 locks, because that is a property of the event.

## Environment, because this cost an hour

Local services do **not** come from `docker compose`: the agent proxy answers 403 for Docker Hub
(`production.cloudfront.docker.com`), so `postgres:16-alpine` cannot be pulled. A native
PostgreSQL 16 cluster is installed and holds the `vinea` database. After a container restart:

```
install -d -o postgres -g postgres /tmp/pglog
su postgres -c "/usr/lib/postgresql/16/bin/pg_ctl -D /var/lib/postgresql/16/main \
  -o '-c config_file=/etc/postgresql/16/main/postgresql.conf' -l /tmp/pglog/pg.log start"
```

Then `cd backend && env -u DATABASE_URL uv run pytest -q` (the `make be-test` container target
needs a backend image this environment cannot pull either).
