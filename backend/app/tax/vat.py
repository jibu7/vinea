"""The VAT return: a query over `journal_lines`, its tie, and the act of filing it.

Decision 12. Four things are worth reading before the code.

**Why the figures are a query and not a table.** Every amount on this return already exists in
the ledger: the P4 tax engine computed it when the document posted, and `journal_lines` carries
it on two lines — the base line with the code and the tax it attracted, the tax-account line
with the same code. So the return reads those lines and sums them. It never recomputes tax from
a rate, because a return that disagreed with the ledger it reports would be the defect this
shape exists to make impossible.

**Why it is reconciled rather than balanced.** A VAT account moves for reasons a return does not
declare — the payment to the authority, a manual journal somebody tagged with nothing, the
return's own settlement entry. So for each VAT account the return states the account's movement
over the range, what it declares of that movement, and every line making up the difference, one
by one. A report that silently forced those to agree would hide exactly the entries an
accountant needs to see.

**Why a filed return never changes.** `high_water_entry_id`: journal entries are append-only
with monotonic ids, so "what this return reported" is "entries dated in the range with an id at
or below the mark". An entry posted into a filed month afterwards has an id above that mark, so
it is *unreported*, and the next return declares it under late entries with its own date.
Nothing about a filed return is recomputed — `figures` is the snapshot as submitted.

**Why a late entry is declared here and not there.** The filed period is closed, so if this
return only *listed* a late entry without adding it to the totals, the tax on it would never be
declared to anybody. It therefore counts in the figures and appears in the late-entry list with
its own date, and the tie carries its total separately so the difference against this range's
account movement still adds up.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import Row, Select, and_, func, or_, select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.core.errors import NotFoundError
from app.kernel import posting
from app.kernel.errors import LedgerStateError, PostingError
from app.kernel.events import LineSpec, ReversalRequested, VatReturnPosted
from app.kernel.money import ZERO
from app.kernel.sequences import DocType, claim_number
from app.models.fiscalization import VatReturn, VatReturnStatus
from app.models.gl import AccountClass, GLAccount
from app.models.journal import JournalEntry, JournalLine
from app.models.tax import TaxCode, TaxNature
from app.models.user import User
from app.services.audit import record_audit

#: `journal_entries.source_doc_type` for a settlement entry, so the GL entry screen can drill
#: from the entry back to the return that posted it.
VAT_RETURN_SOURCE = "vat_return"

#: The module a VAT settlement entry posts under. Its lines sit on the VAT accounts carrying the
#: tax codes' ids (decision 12), so they have to be told apart from the tax lines they settle —
#: otherwise filing a return would add its own payment to the next return's output VAT. The
#: discriminator is *who posted it* rather than an amount, because a settlement line and a real
#: tax line are indistinguishable by amount: both carry `tax_amount = 0` on the tax account.
TAX_MODULE = "tax"

#: The seeded input code for VAT paid at customs, which decision 12 reports as its own section.
#: A code string rather than a column: the return needs no new schema to tell it apart, and
#: decision 14 assigned the schema to step 1. A second country seeds its own import code and
#: names it here; everything else in this module is keyed on `nature`, which is neutral.
IMPORT_TAX_CODE = "VAT-IN-IMP"

#: Which side of the return a line lands on. `output` and `input` say it themselves. An exempt
#: or zero-rated code is used by **both** sales and purchases — one `VAT-EXEMPT` code serves a
#: sale and a purchase alike — so those are decided by the class of the account the base line
#: posted to: income is a sale, anything else is a purchase.
SALES = "sales"
PURCHASES = "purchases"

#: Sales are presented in their natural positive sense and the ledger holds a sale as a credit,
#: so the sales side is negated on the way out. The tie reports the raw ledger movement instead,
#: which is what lets the two be compared without either being "the wrong sign".
_SIGN: dict[str, Decimal] = {SALES: Decimal(-1), PURCHASES: Decimal(1)}

_SIDE_BY_NATURE: dict[TaxNature, str] = {
    TaxNature.OUTPUT: SALES,
    TaxNature.INPUT: PURCHASES,
}


@dataclass(frozen=True)
class CodeTotal:
    """One tax code's contribution, in the section's natural sense (positive for a sale).

    `base`/`tax` are this range's; `late_base`/`late_tax` came out of a period already filed and
    are declared here. `declared_*` is what goes on the return.
    """

    tax_code_id: int
    code: str
    name: str
    nature: TaxNature
    rate_pct: Decimal
    side: str
    base: Decimal
    tax: Decimal
    late_base: Decimal = ZERO
    late_tax: Decimal = ZERO

    @property
    def declared_base(self) -> Decimal:
        return self.base + self.late_base

    @property
    def declared_tax(self) -> Decimal:
        return self.tax + self.late_tax


@dataclass(frozen=True)
class LineMovement:
    """One journal line, named well enough to be looked up from the screen that shows it."""

    line_id: int
    entry_id: int
    entry_number: str
    entry_date: date
    doc_type: str
    module: str
    description: str | None
    base_amount: Decimal


@dataclass(frozen=True)
class AccountTie:
    """A VAT account: what it moved, what this return declares of that movement, and the rest.

    `difference` is `movement - declared_in_range`, and `untagged` is every line that makes it
    up — so `reconciled` is a statement about the report, not a hope. `late_total` sits beside
    them because a late entry is declared on this return and moved this account in an earlier
    range: it is part of the figures and no part of this movement, and saying so is the only way
    the two numbers can be read together.
    """

    account_id: int
    code: str
    name: str
    movement: Decimal
    declared_in_range: Decimal
    late_total: Decimal
    difference: Decimal
    untagged: tuple[LineMovement, ...]

    @property
    def reconciled(self) -> bool:
        """Every franc of the difference is a line this report names."""
        return self.difference == sum((line.base_amount for line in self.untagged), ZERO)


@dataclass(frozen=True)
class LateEntry:
    """An entry posted into a range some return has already filed: declared here, dated there."""

    entry_id: int
    entry_number: str
    entry_date: date
    filed_return_number: str
    tax_code_id: int
    code: str
    side: str
    base: Decimal
    tax: Decimal


@dataclass(frozen=True)
class VatReturnView:
    """Everything the return says, before anybody decides to file it."""

    period_from: date
    period_to: date
    high_water_entry_id: int
    codes: tuple[CodeTotal, ...]
    late_entries: tuple[LateEntry, ...]
    ties: tuple[AccountTie, ...]

    @property
    def sales_standard(self) -> tuple[Decimal, Decimal]:
        return self._totals(SALES, TaxNature.OUTPUT)

    @property
    def sales_zero_rated(self) -> Decimal:
        return self._totals(SALES, TaxNature.ZERO_RATED)[0]

    @property
    def sales_exempt(self) -> Decimal:
        return self._totals(SALES, TaxNature.EXEMPT)[0]

    @property
    def purchases_standard(self) -> tuple[Decimal, Decimal]:
        return self._totals(PURCHASES, TaxNature.INPUT, imports=False)

    @property
    def purchases_imports(self) -> tuple[Decimal, Decimal]:
        return self._totals(PURCHASES, TaxNature.INPUT, imports=True)

    @property
    def purchases_zero_rated(self) -> Decimal:
        return self._totals(PURCHASES, TaxNature.ZERO_RATED)[0]

    @property
    def purchases_exempt(self) -> Decimal:
        return self._totals(PURCHASES, TaxNature.EXEMPT)[0]

    @property
    def output_vat(self) -> Decimal:
        return self._totals(SALES, TaxNature.OUTPUT)[1]

    @property
    def input_vat(self) -> Decimal:
        return self._totals(PURCHASES, TaxNature.INPUT)[1]

    @property
    def net_payable(self) -> Decimal:
        """Positive is payable to the authority; negative is a credit carried forward."""
        return self.output_vat - self.input_vat

    def _totals(
        self, side: str, nature: TaxNature, *, imports: bool | None = None
    ) -> tuple[Decimal, Decimal]:
        base = ZERO
        tax = ZERO
        for row in self.codes:
            if row.side != side or row.nature is not nature:
                continue
            if imports is not None and (row.code == IMPORT_TAX_CODE) is not imports:
                continue
            base += row.declared_base
            tax += row.declared_tax
        return base, tax

    def as_filed(self) -> dict:
        """The snapshot stored on `vat_returns.figures` — evidence, not a model.

        Decimals go out as strings: this is what was submitted, and a float would restate it.
        """
        return {
            "period_from": self.period_from.isoformat(),
            "period_to": self.period_to.isoformat(),
            "high_water_entry_id": self.high_water_entry_id,
            "sections": {
                "sales_standard_base": str(self.sales_standard[0]),
                "sales_standard_vat": str(self.sales_standard[1]),
                "sales_zero_rated_base": str(self.sales_zero_rated),
                "sales_exempt_base": str(self.sales_exempt),
                "purchases_standard_base": str(self.purchases_standard[0]),
                "purchases_standard_vat": str(self.purchases_standard[1]),
                "purchases_imports_base": str(self.purchases_imports[0]),
                "purchases_imports_vat": str(self.purchases_imports[1]),
                "purchases_zero_rated_base": str(self.purchases_zero_rated),
                "purchases_exempt_base": str(self.purchases_exempt),
                "output_vat": str(self.output_vat),
                "input_vat": str(self.input_vat),
                "net_payable": str(self.net_payable),
            },
            "codes": [
                {
                    "tax_code_id": row.tax_code_id,
                    "code": row.code,
                    "nature": str(row.nature),
                    "rate_pct": str(row.rate_pct),
                    "side": row.side,
                    "base": str(row.base),
                    "tax": str(row.tax),
                    "late_base": str(row.late_base),
                    "late_tax": str(row.late_tax),
                }
                for row in self.codes
            ],
            "late_entries": [
                {
                    "entry_id": late.entry_id,
                    "entry_number": late.entry_number,
                    "entry_date": late.entry_date.isoformat(),
                    "filed_return_number": late.filed_return_number,
                    "code": late.code,
                    "base": str(late.base),
                    "tax": str(late.tax),
                }
                for late in self.late_entries
            ],
            "ties": [
                {
                    "account_code": tie.code,
                    "movement": str(tie.movement),
                    "declared_in_range": str(tie.declared_in_range),
                    "late_total": str(tie.late_total),
                    "difference": str(tie.difference),
                    "untagged": [
                        {
                            "entry_number": line.entry_number,
                            "entry_date": line.entry_date.isoformat(),
                            "description": line.description,
                            "base_amount": str(line.base_amount),
                        }
                        for line in tie.untagged
                    ],
                }
                for tie in self.ties
            ],
        }


# --- The query ------------------------------------------------------------------------------


def compute(
    db: Session, company_id: int, *, period_from: date, period_to: date
) -> VatReturnView:
    """The return over [`period_from`, `period_to`], as it stands right now.

    Nothing here is stored; `file_return` takes this and freezes it.
    """
    if period_to < period_from:
        raise PostingError(
            "A return's range ends before it starts",
            code="vat_period_invalid",
            field_errors={"period_to": ["must be on or after the start date"]},
        )

    codes = {code.id: code for code in _tax_codes(db, company_id)}
    filed = _filed_returns(db, company_id)
    high_water = _high_water(db, company_id)

    in_range = _tagged_totals(
        db, company_id, period_from=period_from, period_to=period_to, codes=codes
    )
    late_rows, late_totals = _late(db, company_id, before=period_from, codes=codes, filed=filed)

    rows: list[CodeTotal] = []
    for tax_code_id in sorted(set(in_range) | set(late_totals)):
        code = codes[tax_code_id]
        here = in_range.get(tax_code_id)
        there = late_totals.get(tax_code_id)
        bucket = here if here is not None else there
        assert bucket is not None  # one of the two put this code in the loop
        side = bucket.side
        rows.append(
            CodeTotal(
                tax_code_id=tax_code_id,
                code=code.code,
                name=code.name,
                nature=code.nature,
                rate_pct=code.rate_pct,
                side=side,
                base=here.base if here else ZERO,
                tax=here.tax if here else ZERO,
                late_base=there.base if there else ZERO,
                late_tax=there.tax if there else ZERO,
            )
        )

    ties = _ties(
        db,
        company_id,
        period_from=period_from,
        period_to=period_to,
        codes=codes,
        rows=rows,
        late=late_rows,
    )
    return VatReturnView(
        period_from=period_from,
        period_to=period_to,
        high_water_entry_id=high_water,
        codes=tuple(rows),
        late_entries=tuple(late_rows),
        ties=tuple(ties),
    )


@dataclass(frozen=True)
class _Bucket:
    side: str
    base: Decimal
    tax: Decimal


def _tax_codes(db: Session, company_id: int) -> Sequence[TaxCode]:
    return list(db.scalars(select(TaxCode).where(TaxCode.company_id == company_id)))


def _high_water(db: Session, company_id: int) -> int:
    """The company's latest entry id. A return's mark is taken over the whole ledger rather
    than over its range, because what makes an entry *late* is that it arrived after the return
    was filed — and an entry dated in the range can only arrive afterwards by having a higher
    id than everything that existed when the mark was taken."""
    return (
        db.scalar(
            select(func.coalesce(func.max(JournalEntry.id), 0)).where(
                JournalEntry.company_id == company_id
            )
        )
        or 0
    )


def _filed_returns(db: Session, company_id: int) -> Sequence[VatReturn]:
    """Posted returns, newest range first. A reversed return reported nothing: its range is open
    again and the entries in it are unreported, which is what makes re-filing it possible."""
    return list(
        db.scalars(
            select(VatReturn)
            .where(
                VatReturn.company_id == company_id,
                VatReturn.status == VatReturnStatus.POSTED,
            )
            .order_by(VatReturn.period_from.desc())
        )
    )


def _line_query(company_id: int) -> Select:
    """Lines joined to their entries, with the account, excluding the settlement entries.

    The exclusion is decision 12's: a settlement line carries a tax code on a tax account and is
    not tax — it is the payment of it. `TAX_MODULE` is the only module that posts those.
    """
    return (
        select(
            JournalLine.id,
            JournalLine.tax_code_id,
            JournalLine.gl_account_id,
            JournalLine.base_amount,
            JournalEntry.id.label("entry_id"),
            JournalEntry.number.label("entry_number"),
            JournalEntry.entry_date,
            JournalEntry.doc_type,
            JournalEntry.module,
            JournalLine.description,
            GLAccount.class_.label("account_class"),
        )
        .join(
            JournalEntry,
            and_(
                JournalEntry.id == JournalLine.entry_id,
                JournalEntry.company_id == JournalLine.company_id,
            ),
        )
        .join(
            GLAccount,
            and_(
                GLAccount.id == JournalLine.gl_account_id,
                GLAccount.company_id == JournalLine.company_id,
            ),
        )
        .where(JournalLine.company_id == company_id, JournalEntry.module != TAX_MODULE)
    )


def _side_of(row: Row, code: TaxCode) -> str:
    """Sales or purchases. The code says it when it can; otherwise the account does."""
    by_nature = _SIDE_BY_NATURE.get(code.nature)
    if by_nature is not None:
        return by_nature
    return SALES if row.account_class == AccountClass.INCOME else PURCHASES


def _accumulate(rows: Sequence[Row], codes: dict[int, TaxCode]) -> dict[int, _Bucket]:
    """Base and tax per code, in each side's natural sense.

    Decision 12's two sums, and the split is the account: a line on the code's own tax account
    **is** the tax; every other line carrying the code is base. `base_amount` rather than
    `tax_amount` for the tax figure, because the tax-account line's base amount is the tax in
    base currency, and the P2 convention leaves `tax_amount` at zero there.
    """
    buckets: dict[int, tuple[str, Decimal, Decimal]] = {}
    for row in rows:
        code = codes.get(row.tax_code_id)
        if code is None:
            continue
        side = _side_of(row, code)
        sign = _SIGN[side]
        is_tax_line = code.gl_account_id is not None and row.gl_account_id == code.gl_account_id
        _, base, tax = buckets.get(row.tax_code_id, (side, ZERO, ZERO))
        if is_tax_line:
            tax += sign * row.base_amount
        else:
            base += sign * row.base_amount
        buckets[row.tax_code_id] = (side, base, tax)
    return {
        tax_code_id: _Bucket(side=side, base=base, tax=tax)
        for tax_code_id, (side, base, tax) in buckets.items()
    }


def _tagged_totals(
    db: Session,
    company_id: int,
    *,
    period_from: date,
    period_to: date,
    codes: dict[int, TaxCode],
) -> dict[int, _Bucket]:
    """This range's own figures.

    Every entry dated in the range counts, with no high-water condition: a new return may not
    overlap a posted one (`vat_period_filed`), so no return has reported these dates before.
    """
    rows = list(
        db.execute(
            _line_query(company_id).where(
                JournalLine.tax_code_id.is_not(None),
                JournalEntry.entry_date >= period_from,
                JournalEntry.entry_date <= period_to,
            )
        )
    )
    return _accumulate(rows, codes)


def _late(
    db: Session,
    company_id: int,
    *,
    before: date,
    codes: dict[int, TaxCode],
    filed: Sequence[VatReturn],
) -> tuple[list[LateEntry], dict[int, _Bucket]]:
    """Entries dated inside a filed range whose id is above that return's mark.

    Ranges cannot overlap, so a date belongs to at most one posted return — which is what makes
    "the mark that should have caught this entry" a single number rather than a search.
    """
    if not filed:
        return [], {}

    windows = [
        and_(
            JournalEntry.entry_date >= ret.period_from,
            JournalEntry.entry_date <= ret.period_to,
            JournalEntry.id > ret.high_water_entry_id,
        )
        for ret in filed
        if ret.period_from < before
    ]
    if not windows:
        return [], {}

    rows = list(
        db.execute(
            _line_query(company_id).where(
                JournalLine.tax_code_id.is_not(None),
                JournalEntry.entry_date < before,
                or_(*windows),
            )
        )
    )
    by_range = {(ret.period_from, ret.period_to): ret for ret in filed}

    def owning_return(entry_date: date) -> VatReturn | None:
        for (start, end), ret in by_range.items():
            if start <= entry_date <= end:
                return ret
        return None

    late: list[LateEntry] = []
    for row in rows:
        code = codes.get(row.tax_code_id)
        owner = owning_return(row.entry_date)
        if code is None or owner is None:
            continue
        side = _side_of(row, code)
        sign = _SIGN[side]
        is_tax_line = code.gl_account_id is not None and row.gl_account_id == code.gl_account_id
        late.append(
            LateEntry(
                entry_id=row.entry_id,
                entry_number=row.entry_number,
                entry_date=row.entry_date,
                filed_return_number=owner.number,
                tax_code_id=row.tax_code_id,
                code=code.code,
                side=side,
                base=ZERO if is_tax_line else sign * row.base_amount,
                tax=sign * row.base_amount if is_tax_line else ZERO,
            )
        )
    late.sort(key=lambda row: (row.entry_date, row.entry_number, row.code))
    return late, _accumulate(rows, codes)


def _ties(
    db: Session,
    company_id: int,
    *,
    period_from: date,
    period_to: date,
    codes: dict[int, TaxCode],
    rows: Sequence[CodeTotal],
    late: Sequence[LateEntry],
) -> list[AccountTie]:
    """One tie per VAT **account**: its movement, what the return declares of it, and the rest.

    Per account rather than per code, because an account is the tax account of as many codes as
    a company cares to define — the Rwanda seed alone puts `VAT-IN-18` and `VAT-IN-IMP` on
    `1400`. A tie keyed one-code-per-account would attribute the account to whichever code it
    happened to hold and list the other's tax as untagged, which is the first thing this test
    caught.

    The settlement entries are back in scope here, deliberately: a VAT payment is exactly the
    kind of movement the difference exists to surface. They are excluded from the *declared*
    side by `_line_query` and listed on the untagged side by `_untagged`.
    """
    by_account: dict[int, list[TaxCode]] = {}
    for code in codes.values():
        if code.gl_account_id is not None:
            by_account.setdefault(int(code.gl_account_id), []).append(code)
    if not by_account:
        return []

    account_rows = list(
        db.execute(
            select(GLAccount.id, GLAccount.code, GLAccount.name).where(
                GLAccount.company_id == company_id, GLAccount.id.in_(by_account)
            )
        )
    )
    movement_rows = dict(
        db.execute(
            select(JournalLine.gl_account_id, func.coalesce(func.sum(JournalLine.base_amount), 0))
            .join(
                JournalEntry,
                and_(
                    JournalEntry.id == JournalLine.entry_id,
                    JournalEntry.company_id == JournalLine.company_id,
                ),
            )
            .where(
                JournalLine.company_id == company_id,
                JournalLine.gl_account_id.in_(by_account),
                JournalEntry.entry_date >= period_from,
                JournalEntry.entry_date <= period_to,
            )
            .group_by(JournalLine.gl_account_id)
        ).all()
    )

    ties: list[AccountTie] = []
    for account in account_rows:
        account_codes = {code.id for code in by_account[account.id]}
        # Back into ledger sense, per code: the figures are presented in each side's natural
        # sense and the movement is raw, so they can only be compared in one of the two.
        declared_in_range = sum(
            (row.tax * _SIGN[row.side] for row in rows if row.tax_code_id in account_codes),
            ZERO,
        )
        late_total = sum(
            (row.tax * _SIGN[row.side] for row in late if row.tax_code_id in account_codes),
            ZERO,
        )
        movement = Decimal(movement_rows.get(account.id, ZERO))
        untagged = _untagged(
            db,
            company_id,
            account_id=account.id,
            tax_code_ids=account_codes,
            period_from=period_from,
            period_to=period_to,
        )
        ties.append(
            AccountTie(
                account_id=account.id,
                code=account.code,
                name=account.name,
                movement=movement,
                declared_in_range=declared_in_range,
                late_total=late_total,
                difference=movement - declared_in_range,
                untagged=untagged,
            )
        )
    ties.sort(key=lambda tie: tie.code)
    return ties


def _untagged(
    db: Session,
    company_id: int,
    *,
    account_id: int,
    tax_code_ids: set[int],
    period_from: date,
    period_to: date,
) -> tuple[LineMovement, ...]:
    """Every line on this VAT account in the range that the return does not declare as tax:
    a line with no code, a line carrying a code this account is not the tax account of, and the
    settlement entries."""
    rows = db.execute(
        select(
            JournalLine.id,
            JournalEntry.id.label("entry_id"),
            JournalEntry.number.label("entry_number"),
            JournalEntry.entry_date,
            JournalEntry.doc_type,
            JournalEntry.module,
            JournalLine.description,
            JournalLine.base_amount,
        )
        .join(
            JournalEntry,
            and_(
                JournalEntry.id == JournalLine.entry_id,
                JournalEntry.company_id == JournalLine.company_id,
            ),
        )
        .where(
            JournalLine.company_id == company_id,
            JournalLine.gl_account_id == account_id,
            JournalEntry.entry_date >= period_from,
            JournalEntry.entry_date <= period_to,
            or_(
                JournalLine.tax_code_id.is_(None),
                JournalLine.tax_code_id.not_in(tax_code_ids),
                JournalEntry.module == TAX_MODULE,
            ),
        )
        .order_by(JournalEntry.entry_date, JournalEntry.number, JournalLine.line_no)
    ).all()
    return tuple(
        LineMovement(
            line_id=row.id,
            entry_id=row.entry_id,
            entry_number=row.entry_number,
            entry_date=row.entry_date,
            doc_type=row.doc_type,
            module=row.module,
            description=row.description,
            base_amount=row.base_amount,
        )
        for row in rows
    )


# --- Filing -----------------------------------------------------------------------------------


def file_return(
    db: Session,
    company_id: int,
    *,
    period_from: date,
    period_to: date,
    actor: User,
    idempotency_key: str | None = None,
    idempotency_hash: str | None = None,
    request: Request | None = None,
) -> VatReturn:
    """Freeze the return over the range and post its settlement entry. Never commits.

    The settlement entry is what moves the liability off the VAT accounts and onto one account
    an accountant recognises: Dr each output account for what was declared on it, Cr each input
    account, and the net to `gl_settings.vat_settlement_account_id`. A net credit position sits
    as a debit on that same account rather than on a second one — one account, one balance.
    """
    replayed = _replay(db, company_id, idempotency_key, idempotency_hash)
    if replayed is not None:
        return replayed

    _refuse_an_overlap(db, company_id, period_from=period_from, period_to=period_to)
    view = compute(db, company_id, period_from=period_from, period_to=period_to)
    settlement_account_id = _settlement_account(db, company_id)

    claimed = claim_number(db, company_id, DocType.VAT_RETURN)
    lines = _settlement_lines(db, company_id, view, settlement_account_id)
    entry = None
    if lines:
        entry = posting.post(
            db,
            VatReturnPosted(
                entry_date=period_to,
                description=f"VAT return {claimed.number} "
                f"({period_from.isoformat()} — {period_to.isoformat()})",
                reference=claimed.number,
                lines=tuple(lines),
                source_doc_type=VAT_RETURN_SOURCE,
                idempotency_key=f"{claimed.number}:settlement" if idempotency_key else None,
            ),
            company_id=company_id,
            actor=actor,
        )

    filed = VatReturn(
        company_id=company_id,
        number=claimed.number,
        period_from=period_from,
        period_to=period_to,
        figures=view.as_filed(),
        high_water_entry_id=view.high_water_entry_id,
        journal_entry_id=entry.id if entry is not None else None,
        output_vat=view.output_vat,
        input_vat=view.input_vat,
        net_payable=view.net_payable,
        status=VatReturnStatus.POSTED,
        filed_by=actor.id,
        filed_at=datetime.now(UTC),
        idempotency_key=idempotency_key,
        idempotency_hash=idempotency_hash,
    )
    db.add(filed)
    db.flush()

    # The entry names the return it settles, so the GL drills back to it (`sources.py`).
    if entry is not None:
        entry.source_doc_id = filed.id
        db.flush()

    record_audit(
        db,
        company_id=company_id,
        action="vat_return.file",
        entity="vat_return",
        entity_id=filed.id,
        actor_user_id=actor.id,
        actor_email=actor.email,
        after={
            "number": filed.number,
            "period_from": period_from.isoformat(),
            "period_to": period_to.isoformat(),
            "output_vat": str(filed.output_vat),
            "input_vat": str(filed.input_vat),
            "net_payable": str(filed.net_payable),
            "high_water_entry_id": filed.high_water_entry_id,
            "journal_entry_id": filed.journal_entry_id,
        },
        request=request,
    )
    return filed


def reverse_return(
    db: Session,
    company_id: int,
    return_id: int,
    *,
    reason: str,
    actor: User,
    request: Request | None = None,
) -> VatReturn:
    """Take a filed return back out: the settlement entry reverses, the range reopens.

    Only through here. The entry's module is `tax`, so the kernel refuses a reversal asked for
    from the general ledger (`reverse_via_module_document`) — which is what keeps
    `vat_returns.status` from saying `posted` over an entry that no longer stands.

    What reopens is the *range*: a reversed return reported nothing, so every entry in it is
    unreported again and the period can be filed afresh. That is also why the figures are kept
    rather than cleared — the row is evidence of what was submitted, and a submission that was
    withdrawn is still a submission.
    """
    filed = db.scalar(
        select(VatReturn).where(VatReturn.company_id == company_id, VatReturn.id == return_id)
    )
    if filed is None:
        raise NotFoundError("VAT return not found")
    if filed.status is VatReturnStatus.REVERSED:
        raise LedgerStateError(
            f"{filed.number} has already been reversed",
            code="vat_return_reversed",
            field_errors={"return_id": ["already reversed"]},
        )

    reversal = None
    if filed.journal_entry_id is not None:
        with posting.module_reversal(TAX_MODULE):
            reversal = posting.post(
                db,
                ReversalRequested(
                    entry_date=filed.period_to,
                    entry_id=filed.journal_entry_id,
                    reason=reason,
                    description=f"Reversal of VAT return {filed.number}",
                ),
                company_id=company_id,
                actor=actor,
            )
    filed.status = VatReturnStatus.REVERSED
    filed.reversal_entry_id = reversal.id if reversal is not None else None
    db.flush()

    record_audit(
        db,
        company_id=company_id,
        action="vat_return.reverse",
        entity="vat_return",
        entity_id=filed.id,
        actor_user_id=actor.id,
        actor_email=actor.email,
        before={"status": str(VatReturnStatus.POSTED)},
        after={
            "status": str(VatReturnStatus.REVERSED),
            "reason": reason,
            "reversal_entry_id": filed.reversal_entry_id,
        },
        request=request,
    )
    return filed


def _replay(
    db: Session, company_id: int, key: str | None, request_hash: str | None
) -> VatReturn | None:
    if not key:
        return None
    filed = db.scalar(
        select(VatReturn).where(
            VatReturn.company_id == company_id, VatReturn.idempotency_key == key
        )
    )
    if filed is None:
        return None
    if request_hash is not None and filed.idempotency_hash != request_hash:
        raise LedgerStateError(
            f"Idempotency-Key {key} was already used for a different request ({filed.number}); "
            "use a new key",
            code="idempotency_key_reused",
        )
    return filed


def _refuse_an_overlap(
    db: Session, company_id: int, *, period_from: date, period_to: date
) -> None:
    """A range may not overlap a posted return, by so much as a day.

    Two returns over overlapping ranges would declare the same entry twice, and the high-water
    rule could not tell which of them should have caught a late one — the mark it compares
    against stops being a single number. A reversed return is no obstacle: it declared nothing.
    """
    clash = db.scalar(
        select(VatReturn)
        .where(
            VatReturn.company_id == company_id,
            VatReturn.status == VatReturnStatus.POSTED,
            VatReturn.period_from <= period_to,
            VatReturn.period_to >= period_from,
        )
        .order_by(VatReturn.period_from)
    )
    if clash is not None:
        raise LedgerStateError(
            f"{clash.number} is already filed over "
            f"{clash.period_from.isoformat()} — {clash.period_to.isoformat()}, which this "
            "range overlaps. Reverse it first, or file the period that follows it.",
            code="vat_period_filed",
            field_errors={"period_from": [f"overlaps {clash.number}"]},
        )


def _settlement_account(db: Session, company_id: int) -> int:
    settings = posting.gl_settings_for(db, company_id)
    if settings.vat_settlement_account_id is None:
        raise PostingError(
            "Set the VAT settlement account in GL defaults before filing a return",
            code="gl_setting_missing",
            field_errors={"vat_settlement_account_id": ["required"]},
        )
    return int(settings.vat_settlement_account_id)


def _settlement_lines(
    db: Session, company_id: int, view: VatReturnView, settlement_account_id: int
) -> list[LineSpec]:
    """One line per VAT account carrying the code declared on it, then the net.

    Per code rather than per side, because several codes can share one account and the line is
    what the *next* return's tie reads: a line that named no code would be untagged for the
    right reason and unattributable for the wrong one. `tax_amount` stays zero on every one of
    them — this entry pays the tax, it does not attract any (decision 12).
    """
    codes = {code.id: code for code in _tax_codes(db, company_id)}
    lines: list[LineSpec] = []
    for row in view.codes:
        account_id = codes[row.tax_code_id].gl_account_id
        if account_id is None or row.declared_tax == ZERO:
            continue
        # Back into ledger sense, then reversed: declaring output VAT credited the account, so
        # settling it debits the same account by the same amount.
        amount = row.declared_tax * _SIGN[row.side] * Decimal(-1)
        lines.append(
            LineSpec(
                amount=amount,
                gl_account_id=account_id,
                tax_code_id=row.tax_code_id,
                tax_amount=ZERO,
                description=f"{row.code} settled",
            )
        )
    if not lines:
        return []
    net = -sum((line.amount for line in lines), ZERO)
    if net != ZERO:
        lines.append(
            LineSpec(
                amount=net,
                gl_account_id=settlement_account_id,
                description="VAT payable to the authority"
                if net < ZERO
                else "VAT credit carried forward",
            )
        )
    return lines
