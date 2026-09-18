"""The VAT return API (P7 decision 12).

The screens arrive at **step 7** — Transactions → Tax → VAT returns, with the preview, the tie,
File and Reverse — so every mutating endpoint here carries a `GAP (P7, step 7)` line in
`tests/test_api_has_a_caller.py` naming the step that deletes it.

The preview and the filed detail are deliberately different shapes. A preview is *computed*, so
it carries the sections, the tie and the late entries as objects a screen can lay out. A filed
return carries `figures` — the JSON exactly as submitted — because what a return said is what
it said, and rendering it from a fresh computation would quietly restate it the moment somebody
posted into the period.
"""

from datetime import date

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, get_tenant_context  # noqa: F401
from app.api.idempotency import IdempotencyKey
from app.core import permissions
from app.core.errors import NotFoundError
from app.db import get_db
from app.models.fiscalization import VatReturn
from app.schemas.tax import (
    AccountTieRead,
    CodeTotalRead,
    LateEntryRead,
    LineMovementRead,
    VatReturnDetail,
    VatReturnFile,
    VatReturnPreview,
    VatReturnRead,
    VatReturnReverse,
    VatReturnSections,
)
from app.tax import annexes as annex_service
from app.tax import vat as vat_service

router = APIRouter(prefix="/tax", tags=["tax"])


def _preview_of(view: vat_service.VatReturnView) -> VatReturnPreview:
    return VatReturnPreview(
        period_from=view.period_from,
        period_to=view.period_to,
        high_water_entry_id=view.high_water_entry_id,
        sections=VatReturnSections(
            sales_standard_base=view.sales_standard[0],
            sales_standard_vat=view.sales_standard[1],
            sales_zero_rated_base=view.sales_zero_rated,
            sales_exempt_base=view.sales_exempt,
            purchases_standard_base=view.purchases_standard[0],
            purchases_standard_vat=view.purchases_standard[1],
            purchases_imports_base=view.purchases_imports[0],
            purchases_imports_vat=view.purchases_imports[1],
            purchases_zero_rated_base=view.purchases_zero_rated,
            purchases_exempt_base=view.purchases_exempt,
            output_vat=view.output_vat,
            input_vat=view.input_vat,
            net_payable=view.net_payable,
        ),
        codes=[
            CodeTotalRead(
                tax_code_id=row.tax_code_id,
                code=row.code,
                name=row.name,
                rate_pct=row.rate_pct,
                side=row.side,
                base=row.base,
                tax=row.tax,
                late_base=row.late_base,
                late_tax=row.late_tax,
                declared_base=row.declared_base,
                declared_tax=row.declared_tax,
            )
            for row in view.codes
        ],
        late_entries=[
            LateEntryRead(
                entry_id=late.entry_id,
                entry_number=late.entry_number,
                entry_date=late.entry_date,
                filed_return_number=late.filed_return_number,
                code=late.code,
                side=late.side,
                base=late.base,
                tax=late.tax,
            )
            for late in view.late_entries
        ],
        ties=[
            AccountTieRead(
                account_id=tie.account_id,
                code=tie.code,
                name=tie.name,
                movement=tie.movement,
                declared_in_range=tie.declared_in_range,
                late_total=tie.late_total,
                difference=tie.difference,
                reconciled=tie.reconciled,
                untagged=[
                    LineMovementRead(
                        line_id=line.line_id,
                        entry_id=line.entry_id,
                        entry_number=line.entry_number,
                        entry_date=line.entry_date,
                        doc_type=line.doc_type,
                        module=line.module,
                        description=line.description,
                        base_amount=line.base_amount,
                    )
                    for line in tie.untagged
                ],
            )
            for tie in view.ties
        ],
    )


@router.get("/vat-returns/preview")
def preview_vat_return(
    period_from: date = Query(...),
    period_to: date = Query(...),
    auth: AuthContext = permissions.require(permissions.TAX_VAT_RETURN_VIEW),
    db: Session = Depends(get_db),
) -> VatReturnPreview:
    """The return over a range, computed now and stored nowhere."""
    return _preview_of(
        vat_service.compute(
            db, auth.company_id, period_from=period_from, period_to=period_to
        )
    )


@router.get("/vat-returns")
def list_vat_returns(
    auth: AuthContext = permissions.require(permissions.TAX_VAT_RETURN_VIEW),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[VatReturnRead]:
    rows = db.scalars(
        select(VatReturn)
        .where(VatReturn.company_id == auth.company_id)
        .order_by(VatReturn.period_from.desc(), VatReturn.id.desc())
        .limit(limit)
    )
    return [VatReturnRead.model_validate(row) for row in rows]


@router.get("/vat-returns/{return_id}")
def read_vat_return(
    return_id: int,
    auth: AuthContext = permissions.require(permissions.TAX_VAT_RETURN_VIEW),
    db: Session = Depends(get_db),
) -> VatReturnDetail:
    filed = db.scalar(
        select(VatReturn).where(
            VatReturn.company_id == auth.company_id, VatReturn.id == return_id
        )
    )
    if filed is None:
        raise NotFoundError("VAT return not found")
    return VatReturnDetail.model_validate(filed)


@router.post("/vat-returns", status_code=status.HTTP_201_CREATED)
def file_vat_return(
    payload: VatReturnFile,
    request: Request,
    auth: AuthContext = permissions.require(permissions.TAX_VAT_RETURN_FILE),
    db: Session = Depends(get_db),
    idempotency_key: str = IdempotencyKey,
) -> VatReturnDetail:
    """Freeze the return and post its settlement entry.

    `Idempotency-Key` because filing is not naturally idempotent from the caller's side: a
    double-click would otherwise file the same period twice, and the second would be refused
    `vat_period_filed` with no way to tell a duplicate submission from a genuine mistake.
    """
    filed = vat_service.file_return(
        db,
        auth.company_id,
        period_from=payload.period_from,
        period_to=payload.period_to,
        actor=auth.user,
        idempotency_key=idempotency_key,
        request=request,
    )
    db.commit()
    return VatReturnDetail.model_validate(filed)


@router.post("/vat-returns/{return_id}/reverse")
def reverse_vat_return(
    return_id: int,
    payload: VatReturnReverse,
    request: Request,
    auth: AuthContext = permissions.require(permissions.TAX_VAT_RETURN_FILE),
    db: Session = Depends(get_db),
) -> VatReturnDetail:
    filed = vat_service.reverse_return(
        db,
        auth.company_id,
        return_id,
        reason=payload.reason,
        actor=auth.user,
        request=request,
    )
    db.commit()
    return VatReturnDetail.model_validate(filed)


@router.get("/vat-returns/annexes/sales.csv")
def sales_annex(
    period_from: date = Query(...),
    period_to: date = Query(...),
    auth: AuthContext = permissions.require(permissions.TAX_VAT_RETURN_VIEW),
    db: Session = Depends(get_db),
) -> Response:
    body = annex_service.sales_csv(
        db, auth.company_id, period_from=period_from, period_to=period_to
    )
    return _csv_response(body, f"vat-sales-{period_from:%Y%m%d}-{period_to:%Y%m%d}.csv")


@router.get("/vat-returns/annexes/purchases.csv")
def purchases_annex(
    period_from: date = Query(...),
    period_to: date = Query(...),
    auth: AuthContext = permissions.require(permissions.TAX_VAT_RETURN_VIEW),
    db: Session = Depends(get_db),
) -> Response:
    body = annex_service.purchases_csv(
        db, auth.company_id, period_from=period_from, period_to=period_to
    )
    return _csv_response(body, f"vat-purchases-{period_from:%Y%m%d}-{period_to:%Y%m%d}.csv")


def _csv_response(body: str, filename: str) -> Response:
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
