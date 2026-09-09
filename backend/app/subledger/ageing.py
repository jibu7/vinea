"""Ageing — historical, not a snapshot (P4 decision 9).

An as-of ageing nets only documents dated on or before the as-of date and allocations with
`allocation_date` on or before it, so re-running last month's age analysis today reproduces
last month's numbers exactly. The basis (document date or due date) belongs to the bucket
set, not to the report call.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.partner import AgeingBasis, AgeingBucket, AgeingBucketSet, Partner, PartnerRole
from app.subledger import masters
from app.subledger.openitems import OpenItem, open_items_as_of

ZERO = Decimal(0)


@dataclass
class BucketAmount:
    label: str
    from_days: int
    to_days: int | None
    amount: Decimal = ZERO


@dataclass
class AgeingRow:
    partner_id: int
    partner_code: str | None
    partner_name: str
    buckets: list[BucketAmount]
    total: Decimal = ZERO


@dataclass
class Ageing:
    role: PartnerRole
    as_of: date
    bucket_set: AgeingBucketSet
    buckets: list[AgeingBucket]
    rows: list[AgeingRow] = field(default_factory=list)

    @property
    def totals(self) -> list[BucketAmount]:
        totals = [
            BucketAmount(bucket.label, bucket.from_days, bucket.to_days) for bucket in self.buckets
        ]
        for row in self.rows:
            for index, cell in enumerate(row.buckets):
                totals[index].amount += cell.amount
        return totals

    @property
    def grand_total(self) -> Decimal:
        return sum((row.total for row in self.rows), ZERO)


def age_of(item: OpenItem, basis: AgeingBasis, as_of: date) -> int:
    reference = item.document.document_date
    if basis == AgeingBasis.DUE_DATE and item.document.due_date is not None:
        reference = item.document.due_date
    return max((as_of - reference).days, 0)


def bucket_index(buckets: list[AgeingBucket], days: int) -> int:
    for index, bucket in enumerate(buckets):
        if bucket.contains(days):
            return index
    return len(buckets) - 1


def age_analysis(
    db: Session,
    company_id: int,
    role: PartnerRole,
    *,
    as_of: date,
    bucket_set_id: int | None = None,
    partner_id: int | None = None,
) -> Ageing:
    bucket_set = (
        masters.get_bucket_set(db, company_id, bucket_set_id)
        if bucket_set_id is not None
        else masters.default_bucket_set(db, company_id)
    )
    buckets = masters.buckets_of(db, bucket_set)
    items = open_items_as_of(
        db, company_id, role=role, partner_id=partner_id, as_of=as_of
    )
    partners = {
        partner.id: partner
        for partner in db.scalars(select(Partner).where(Partner.company_id == company_id))
    }

    rows: dict[int, AgeingRow] = {}
    for item in items:
        partner = partners.get(item.document.partner_id)
        if partner is None:
            continue
        row = rows.get(partner.id)
        if row is None:
            row = AgeingRow(
                partner_id=partner.id,
                partner_code=partner.code_for(role),
                partner_name=partner.name,
                buckets=[
                    BucketAmount(bucket.label, bucket.from_days, bucket.to_days)
                    for bucket in buckets
                ],
            )
            rows[partner.id] = row
        index = bucket_index(buckets, age_of(item, bucket_set.basis, as_of))
        row.buckets[index].amount += item.signed_base_amount
        row.total += item.signed_base_amount

    return Ageing(
        role=role,
        as_of=as_of,
        bucket_set=bucket_set,
        buckets=buckets,
        rows=sorted(rows.values(), key=lambda row: row.partner_name),
    )
