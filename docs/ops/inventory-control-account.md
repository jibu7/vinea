# Turning on inventory for a tenant whose inventory account was skipped

**Who this is for:** a tenant provisioned before P5 whose `1300 Inventory` (or `1350 Stock in
Transit`) already carried journal lines when migration `0012_p5_masters` ran. For those
companies the migration deliberately did **not** mark the account as an INV control account,
and left `gl_settings.inventory_account_id` NULL. The upgrade output named them, one line per
company:

```
0012_p5_masters: company 7: account 1300 already carries 12 journal line(s) and is NOT being
marked as an inventory control account. Its gl_settings key stays NULL and inventory posting
will refuse to start for this company until the account is migrated by hand.
```

Everything still works for that tenant except inventory, which refuses to post with
`gl_setting_missing` rather than posting into an account nothing is guarding. This document is
the way out. It is deliberately manual: the decision to narrow an account permanently belongs
to a person who can look at that company's history, not to a migration that cannot.

Why the migration would not do it: marking an account `inventory` makes two things permanent —
only `module='inv'` may post to it, and every line on it must carry an `item_id`. Pre-P5 lines
satisfy neither, and posted lines are append-only (architecture rule 3), so they cannot be
corrected into compliance. See `_mark_inventory_control_accounts` in `0012_p5_masters.py`.

## Before you start

Read this first, because **step 1 must happen before step 2 and cannot be done afterwards**:
once 1300 carries a control type, a manual journal can no longer touch it
(`control_account_direct_posting`). Doing step 2 first strands the balance permanently.

You need: `gl:journal_post`, `inv:setup_manage`, `inv:transactions_adjust`, an open period, and
database access for step 2.

## The path

### 1. Journal the old balance out to suspense — while 1300 is still an ordinary account

In **General Ledger → Journal Entry**, post one entry per branch that moves the whole current
balance of 1300 to `3400 Opening Balance Suspense` (the seed pack adds 3400 for exactly this).
Date it in an open period; this is the date from which the tenant's stock will reconcile.

Use the trial balance to read the per-branch balances. After this entry 1300 nets to zero and
3400 holds what it used to.

Do the same for 1350 if it too has history.

### 2. Mark the accounts as inventory control accounts

There is no screen for this, on purpose: an account's control type is not a setting an
operator should be able to toggle, because toggling it changes what the ledger is allowed to
say. It is a support action, run against the tenant's database:

```sql
-- Support-only. Run once, for one company, after step 1 has left the accounts at zero.
UPDATE gl_accounts
   SET is_control = true, control_type = 'inventory'
 WHERE company_id = :company_id
   AND code IN ('1300', '1350')
   AND control_type IS NULL;
```

Check first that the balance really is zero — this statement does not, because at this point
the only thing that can tell you the journal in step 1 was complete is the trial balance:

```sql
SELECT a.code, sum(l.base_amount) AS balance
  FROM gl_accounts a LEFT JOIN journal_lines l ON l.gl_account_id = a.id
 WHERE a.company_id = :company_id AND a.code IN ('1300', '1350')
 GROUP BY a.code;
```

### 3. Point the settings at them

In **Maintenance → Inventory → Inventory defaults**, set the inventory account and the
in-transit account. The API accepts them only now: `_assert_inventory_control_setting` refuses
an account that is not an INV control account, which is why this step comes third.

While you are there, check the three contra keys (adjustment, count variance, COGS) and the
negative-stock policy — the migration filled those in for every tenant, but the screen is where
they are confirmed.

### 4. Bring the stock back in through the inventory journal batch

Post the opening stock — item, warehouse, quantity, unit cost — as an **inventory journal
batch** with the `OPEN` (opening stock) transaction type, whose contra is 3400. Date it on or
after the date used in step 1.

That lands the value back on 1300, this time with a `stock_move` and an `item_id` behind every
franc of it, and nets 3400 back to zero.

## What this does and does not fix

From the date of the step-1 journal onward, the tenant's stock valuation equals its inventory
GL balance, per branch and at every date — the P5 invariant holds and the valuation report
ties to the general ledger.

**Before that date it does not, and no procedure can make it.** The pre-P5 lines on 1300 are
posted history: they are still there, still in the trial balance at their own dates, and no
stock move will ever explain them. `assert_stock_invariants` run against a date before the
step-1 journal will fail for this tenant, correctly. That is the price of the account having
been used as an ordinary account before inventory existed, and it is why the migration refused
to pretend otherwise.

If that matters for a particular tenant — an auditor who needs the inventory account to
reconcile across the changeover — the answer is a documented note in that company's accounts,
not a change to the ledger.
