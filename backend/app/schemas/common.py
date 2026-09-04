from pydantic import BaseModel, ConfigDict


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page[ItemT](BaseModel):
    """Cursor pagination (ADR-11) — `next_cursor` is the last id of this page."""

    items: list[ItemT]
    next_cursor: int | None = None


class ErrorEnvelope(BaseModel):
    code: str
    message: str
    field_errors: dict[str, list[str]] = {}
