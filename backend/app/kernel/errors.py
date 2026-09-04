"""Kernel error types and the translation of DB-enforced invariant failures.

The database raises custom SQLSTATEs (class `VN`) from the triggers installed by migration
0002. The Posting Engine validates first in Python (better messages), but the triggers are
the authority, so their errors are mapped to the same envelope rather than surfacing as 500s.
"""

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError

from app.core.errors import AppError

SQLSTATE_IMMUTABLE = "VN001"
SQLSTATE_UNBALANCED = "VN002"
SQLSTATE_NOT_POSTABLE = "VN003"
SQLSTATE_PERIOD_NOT_OPEN = "VN004"
SQLSTATE_TOO_FEW_LINES = "VN005"

_SQLSTATE_CODES = {
    SQLSTATE_IMMUTABLE: "posted_entry_immutable",
    SQLSTATE_UNBALANCED: "unbalanced_entry",
    SQLSTATE_NOT_POSTABLE: "account_not_postable",
    SQLSTATE_PERIOD_NOT_OPEN: "period_not_open",
    SQLSTATE_TOO_FEW_LINES: "too_few_lines",
}


class PostingError(AppError):
    """A posting was refused by a kernel rule (period, account, dimension, balance)."""

    status_code = 422  # starlette renamed its 422 constant; the number is stable
    code = "posting_rejected"


class LedgerStateError(AppError):
    """The requested state transition is not allowed (period/year status, reversal twice)."""

    status_code = status.HTTP_409_CONFLICT
    code = "ledger_state_conflict"


def kernel_sqlstate(exc: BaseException) -> str | None:
    orig = getattr(exc, "orig", exc)
    state = getattr(orig, "sqlstate", None)
    return state if isinstance(state, str) and state in _SQLSTATE_CODES else None


def translate_db_error(exc: DBAPIError) -> PostingError | None:
    state = kernel_sqlstate(exc)
    if state is None:
        return None
    message = str(getattr(exc, "orig", exc)).splitlines()[0]
    return PostingError(message, code=_SQLSTATE_CODES[state])


def install_kernel_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DBAPIError)
    async def _db_error(_: Request, exc: DBAPIError) -> JSONResponse:
        translated = translate_db_error(exc)
        if translated is None:
            raise exc
        return JSONResponse(
            status_code=translated.status_code,
            content={
                "code": translated.code,
                "message": translated.message,
                "field_errors": translated.field_errors,
            },
        )
