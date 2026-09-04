"""The Ledger Kernel (Master Plan §5 P2, ADR-04..08).

- `posting`   — the single GL authority: `post(event)` / `reverse(entry_id)`
- `events`    — typed posting events modules emit
- `money`     — rounding, exchange rates, tax arithmetic
- `sequences` — gapless document numbering
- `periods` / `year_end` — accounting period enforcement and close/reopen
- `balances`  — the verifiable `period_balances` cache
- `enquiries` — trial balance, account transactions
- `accounts`  — chart of accounts maintenance
"""
