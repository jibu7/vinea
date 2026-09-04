"""`platform_scope()` is the one documented way past RLS, so its blast radius is fixed
here: only the four sanctioned code paths may open it, and operator use must be audited.
"""

import ast
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import set_tenant
from app.models.audit import AuditLog

APP_ROOT = Path(__file__).resolve().parents[1] / "app"

# module -> why it is allowed to bypass tenant scoping
SANCTIONED_CALLERS = {
    "db.py": "defines the context manager",
    "services/auth.py": "login and session bootstrap: no tenant is chosen yet",
    "services/invitations.py": "invitation accept: the invitee has no session yet",
    "services/provisioning.py": "signup: the company does not exist yet",
    "services/operator.py": "operator console: deliberately cross-tenant, always audited",
}


def _modules_calling_platform_scope() -> set[str]:
    callers: set[str] = set()
    for path in APP_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            called = node.func if isinstance(node, ast.Call) else None
            name = getattr(called, "id", None) or getattr(called, "attr", None)
            if name == "platform_scope":
                callers.add(path.relative_to(APP_ROOT).as_posix())
        if any(
            isinstance(node, ast.FunctionDef) and node.name == "platform_scope"
            for node in ast.walk(tree)
        ):
            callers.add(path.relative_to(APP_ROOT).as_posix())
    return callers


def test_only_sanctioned_modules_open_platform_scope() -> None:
    callers = _modules_calling_platform_scope()

    unexpected = sorted(callers - set(SANCTIONED_CALLERS))
    assert unexpected == [], (
        "platform_scope() bypasses tenant isolation; new call sites need review: "
        f"{unexpected}"
    )


def test_routers_and_dependencies_never_bypass_tenancy() -> None:
    callers = _modules_calling_platform_scope()

    assert not [module for module in callers if module.startswith("api/")]


def test_the_sanctioned_list_stays_honest() -> None:
    """Fails if a documented caller stops using it, so the allowlist cannot rot."""
    callers = _modules_calling_platform_scope()

    assert sorted(callers) == sorted(SANCTIONED_CALLERS)


def test_operator_console_bypass_leaves_an_audit_trail(
    client: TestClient, two_tenants, platform_admin, db: Session
) -> None:
    first, _ = two_tenants
    operator = TestClient(client.app)
    operator.post(
        "/api/v1/auth/login",
        json={"email": platform_admin.email, "password": "correct horse battery staple"},
    )

    operator.post(
        f"/api/v1/operator/tenants/{first.company.id}/suspend", json={"reason": "Non-payment"}
    )
    operator.post(f"/api/v1/operator/tenants/{first.company.id}/activate")
    operator.post(f"/api/v1/operator/tenants/{first.company.id}/impersonate")

    set_tenant(db, first.company.id)
    entries = db.scalars(
        select(AuditLog).where(AuditLog.action.like("operator.%")).order_by(AuditLog.id)
    ).all()

    assert [entry.action for entry in entries] == [
        "operator.tenant_suspended",
        "operator.tenant_active",
        "operator.impersonate",
    ]
    assert {entry.actor_user_id for entry in entries} == {platform_admin.id}
    assert {entry.company_id for entry in entries} == {first.company.id}
