"""Banking (Master Plan §5 P8).

**Nothing in this package writes a journal line.** Every posting it causes goes through
`post_event` with a `CashbookEntry`, through `post_document()` / `reverse_document()` and
`allocate()` / `unallocate()` from P4, or through the P7 revaluation service — so no new
`module` string ever reaches `journal_entries.module`, and `tests/banking/test_boundary.py`
proves the import graph says so rather than the comment.

The ledger is the truth; a statement is the bank's record of it; a reconciliation is the
proof that the two agree. If a change here would store a balance, adjust a ledger figure to
meet the statement, or turn a statement line into a journal line without a posting, it is
wrong by construction rather than by review.
"""
