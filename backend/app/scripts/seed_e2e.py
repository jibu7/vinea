"""Deterministic fixture data for the frontend Playwright e2e suite (P3 step 8).

Provisions two tenants through the real signup path (`provision_tenant`, same as the
`/auth/signup` route), then adds two pieces of state the HTTP API has no way to produce
without an email round-trip: a second, already-active membership for the secondary owner
(the invite-accept flow needs a mailed token) and one closed accounting period (closing a
period for real goes through year-end close, which needs prior periods closed too — for a
fixture we just need *a* closed period to exercise the `period_closed` error path).

Two extra non-owner members carry a single seeded role each: a Clerk who can only read
(so a permission gate can be shown to be real) and an Accountant who can post AR/AP but
holds no `*:credit_limit_override` (so the credit-limit block can be shown to fire).

The cross-company membership goes on the *secondary* user, not the primary one, on purpose:
`auth_service.select_membership()` only auto-selects a company on login when the user has
exactly one membership, so PRIMARY_EMAIL — used by every spec except the switch-company one —
needs to stay single-membership or login leaves every one of those tests with no active
company and everything company-scoped (e.g. `/gl/accounts`) comes back empty.

Idempotent: safe to run against a database that already has these fixtures — everything is
looked up by its fixed email/name first and only created if missing. Run with
`uv run python -m app.scripts.seed_e2e`.
"""

import json
import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from app.core.permissions import OWNER_ROLE_NAME
from app.db import SessionLocal, platform_scope
from app.models.company import Company
from app.models.fiscal import AccountingPeriod, PeriodStatus
from app.models.membership import CompanyMembership, MembershipRole, MembershipStatus, Role
from app.models.user import User
from app.services.provisioning import ProvisionedTenant, provision_tenant

PASSWORD_ENV = "E2E_PASSWORD"

# `REPO_ROOT` first, then the path relative to this file — the same convention
# `export_api_enums.py` and `tests/test_schema_invariants.py` use. `parents[3]` is the repo
# root on a host checkout and `/` inside the backend container, where the tree is `/app` and
# the repo is bind-mounted read-only at `/repo`.
REPO_ROOT = Path(os.environ.get("REPO_ROOT") or Path(__file__).resolve().parents[3])
DOTENV = REPO_ROOT / ".env"


def password_from_dotenv() -> str:
    """`E2E_PASSWORD` as the repo-root `.env` sets it, or "" if it is not set there.

    Deliberately a **three-line parser** rather than a dependency: this reads one key out of a
    file that `docker compose` already reads, and `python-dotenv` in the production image to
    do it would be a strange trade. Quotes are stripped because `.env` files are commonly
    written with them and `docker compose` strips them too, so a value that works for compose
    has to work here.
    """
    try:
        text = DOTENV.read_text()
    except OSError:
        return ""
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith(f"{PASSWORD_ENV}="):
            continue
        value = line.split("=", 1)[1].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        return value
    return ""


def password_source() -> str:
    """Which of the two places the credential came from, for the seed's summary line."""
    if os.environ.get(PASSWORD_ENV):
        return f"${PASSWORD_ENV}"
    return str(DOTENV) if password_from_dotenv() else "nowhere"


def fixture_password() -> str:
    """The fixture password: the environment first, then the repo-root `.env`.

    One value seeds these users *and* drives the Playwright login, so there is a single place
    it exists and no literal in the tree for it to drift from (P4 step 9).

    **The `.env` fallback is step 9's fix for a drift step 8 hit.** The two sides resolved the
    variable in the same way but not from the same place: `make db-reset` passes the *exported*
    value into the container with `-e E2E_PASSWORD`, while `.env` — which is what the file this
    project asks you to fill in actually is — reached `docker compose` and nothing else. Set
    one and forget to export it, or export one and edit the other, and the seed hashes a
    password the suite does not type. The symptom is a wall of `invalid_credentials` on every
    spec, which reads exactly like a broken branch and is in fact two different strings.

    So both sides now read the environment first and the same `.env` second
    (`frontend/e2e/support/fixtures.ts` does the identical two-step), which makes these the
    supported paths and all three consistent:

      * export `E2E_PASSWORD` and run `make db-reset` — the export wins on both sides;
      * set it in `.env` and run `make db-reset` — the file wins on both sides;
      * CI, which exports a fresh value per run and has no `.env` at all.

    What remains possible is seeding with one value and running the suite *later* against a
    different one, because the database has already been written. That cannot be detected from
    a password, so it is not guessed at: `login()` in the suite names it as the likely cause
    when a seeded fixture user is refused.

    Resolved when the script runs, not at import, so importing this module never explodes.
    """
    password = os.environ.get(PASSWORD_ENV) or password_from_dotenv()
    if not password:
        raise SystemExit(
            f"{PASSWORD_ENV} is not set — not in the environment, and not in {DOTENV}.\n"
            "Either put it in .env (which `docker compose` and this script both read):\n"
            f'  {PASSWORD_ENV}="$(openssl rand -base64 24)"\n'
            "or export it and pass it into the container:\n"
            f'  export {PASSWORD_ENV}="$(openssl rand -base64 24)"\n'
            f"  docker compose exec -e {PASSWORD_ENV} -T backend "
            "uv run python -m app.scripts.seed_e2e\n"
            "Whichever you choose, the Playwright suite resolves it the same way, so the two "
            "cannot disagree."
        )
    return password


# `.example` (RFC 2606) — `email-validator` (backing Pydantic's EmailStr on the login/signup
# routes) explicitly rejects `.test`/`.invalid`/`.localhost` as reserved, but allows `.example`.
PRIMARY_EMAIL = "e2e.primary@vinea.example"
PRIMARY_COMPANY = "Rugari Wines E2E"

SECONDARY_EMAIL = "e2e.secondary@vinea.example"
SECONDARY_COMPANY = "Kivu Traders E2E"

# A read-only member of the primary company, holding the seeded "Clerk" role: gl/ar/ap
# `*_reports_view` and nothing that can write. Every screen that gates its actions on a
# setup permission needs one of these to prove the gate is real rather than decorative
# (P4 step 6's AR/AP defaults).
READONLY_EMAIL = "e2e.readonly@vinea.example"
READONLY_ROLE_NAME = "Clerk"

# A poster who cannot override a credit limit. The seeded "Accountant" role holds
# `ar:transactions_post` and `ar:reports_view` but *not* `ar:credit_limit_override`, which is
# the only way to see the block fire: the owner holds every permission, so an owner would sail
# past the limit and the check would look decorative (P4 step 9's credit-limit tape).
POSTER_EMAIL = "e2e.poster@vinea.example"
POSTER_ROLE_NAME = "Accountant"

# One supplier, so the Suppliers master is not an empty table in screenshots or in the AP
# specs. Customers are created by the specs themselves (they assert on creation); nothing
# asserts on creating a supplier, so the fixture provides one.
SUPPLIER_CODE = "E2ESUP001"
SUPPLIER_NAME = "Musanze Packaging Ltd"

# P8 decision 9 — the foreign-currency bank account, on the primary company only.
USD_BANK_CODE = "1121"
USD_BANK_NAME = "Bank Account USD"

# One customer carrying two *aged* invoices, so the age analysis is a report with figures in
# it rather than a table of zeroes (P4 step 8, shot 9).
#
# The default bucket set is `STD` (30/60/90/120+) and it ages on **due date**, not document
# date — so the due dates, not the document dates, are what place these amounts. Both are set
# explicitly rather than left to payment terms, because the bucket has to be exact:
#
#     dated 100 days back, due 75 days back  -> age 75 -> "61 - 90"
#     dated  45 days back, due on receipt    -> age 45 -> "31 - 60"
#
# Both ages sit mid-bucket, so the seeded data still lands in the same two columns if the
# database is seeded a fortnight before the report is run.
AGED_CUSTOMER_CODE = "E2EAGED01"
AGED_CUSTOMER_NAME = "Gisenyi Hotel Group"
AGED_REVENUE_ACCOUNT = "4100"

# (days back to the document date, days back to the due date, amount, description)
AGED_INVOICES: tuple[tuple[int, int, Decimal, str], ...] = (
    (100, 75, Decimal(450_000), "Conference catering"),
    (45, 45, Decimal(275_000), "Function room hire"),
)


def _aged_document_dates(today: date) -> list[date]:
    return [today - timedelta(days=doc_days) for doc_days, _, _, _ in AGED_INVOICES]


def _existing_tenant(db, *, email: str) -> tuple[User, Company] | None:
    with platform_scope(db):
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            return None
        company = db.scalar(
            select(Company)
            .join(CompanyMembership, CompanyMembership.company_id == Company.id)
            .where(CompanyMembership.user_id == user.id, CompanyMembership.is_owner.is_(True))
        )
        if company is None:
            raise RuntimeError(f"user {email} exists but owns no company — inconsistent fixture")
        return user, company


def _ensure_fixture_password(db, *, user: User, password: str) -> None:
    """Put `password` on an existing fixture user whose stored hash no longer matches.

    Idempotency for a credential has to mean the end state matches the input, not that a row
    that already exists is left alone. CI mints a fresh `E2E_PASSWORD` per run and a
    developer's database already holds these fixtures from the last one, so returning the old
    hash untouched made this script report success while every later login 401'd with
    `invalid_credentials` — the exact silent disagreement between seed and suite that
    `fixture_password()` refuses to allow, arriving one layer further in.

    Called inside an open `platform_scope`; the caller commits.
    """
    from app.core.security import hash_password, verify_password

    if verify_password(user.hashed_password, password):
        return
    user.hashed_password = hash_password(password)
    db.add(user)


def _get_or_create_tenant(
    db, *, company_name: str, email: str, full_name: str, password: str
) -> tuple[User, Company]:
    existing = _existing_tenant(db, email=email)
    if existing is not None:
        user, company = existing
        with platform_scope(db):
            _ensure_fixture_password(db, user=user, password=password)
            db.commit()
        return user, company
    tenant: ProvisionedTenant = provision_tenant(
        db,
        company_name=company_name,
        full_name=full_name,
        email_address=email,
        password=password,
    )
    db.commit()
    return tenant.user, tenant.company


def _ensure_cross_company_membership(db, *, user: User, company: Company) -> None:
    """Give `user` a second, already-active membership in `company` with full access —
    standing in for an accepted invitation so the switch-company e2e flow has somewhere
    real to switch to."""
    with platform_scope(db):
        existing = db.scalar(
            select(CompanyMembership).where(
                CompanyMembership.company_id == company.id, CompanyMembership.user_id == user.id
            )
        )
        if existing is not None:
            return

        role = db.scalar(
            select(Role).where(Role.company_id == company.id, Role.name == OWNER_ROLE_NAME)
        )
        if role is None:
            raise RuntimeError(f"{OWNER_ROLE_NAME!r} role missing for company {company.id}")

        membership = CompanyMembership(
            company_id=company.id,
            user_id=user.id,
            email=user.email,
            is_owner=False,
            status=MembershipStatus.ACTIVE,
            accepted_at=datetime.now(UTC),
        )
        db.add(membership)
        db.flush()
        db.add(MembershipRole(company_id=company.id, membership_id=membership.id, role_id=role.id))
        db.commit()


def _ensure_member_with_role(
    db, *, company: Company, email: str, full_name: str, role_name: str, password: str
) -> User:
    """An already-active, non-owner membership carrying exactly one seeded role. Created
    directly rather than through the invite flow, which needs a mailed token — the same
    reason `_ensure_cross_company_membership` exists."""
    from app.core.security import hash_password

    with platform_scope(db):
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(
                email=email,
                hashed_password=hash_password(password),
                full_name=full_name,
                email_verified_at=datetime.now(UTC),
            )
            db.add(user)
            db.flush()
        else:
            _ensure_fixture_password(db, user=user, password=password)

        existing = db.scalar(
            select(CompanyMembership).where(
                CompanyMembership.company_id == company.id, CompanyMembership.user_id == user.id
            )
        )
        if existing is not None:
            db.commit()
            return user

        role = db.scalar(
            select(Role).where(Role.company_id == company.id, Role.name == role_name)
        )
        if role is None:
            raise RuntimeError(f"{role_name!r} role missing for company {company.id}")

        membership = CompanyMembership(
            company_id=company.id,
            user_id=user.id,
            email=user.email,
            is_owner=False,
            status=MembershipStatus.ACTIVE,
            accepted_at=datetime.now(UTC),
        )
        db.add(membership)
        db.flush()
        db.add(MembershipRole(company_id=company.id, membership_id=membership.id, role_id=role.id))
        db.commit()
        return user


def _ensure_partners(db, *, company: Company, actor: User) -> str | None:
    """Through the real service with a real actor — never a raw insert, so the seeded row is
    the same shape the application would have written."""
    from app.db import set_tenant
    from app.models.partner import PartnerRole, TaxMode
    from app.subledger import masters

    set_tenant(db, company.id)
    existing = masters.list_partners(db, company.id, role=PartnerRole.AP, include_inactive=True)
    supplier = next((p for p in existing if p.supplier_code == SUPPLIER_CODE), None)
    if supplier is None:
        supplier = masters.create_partner(
            db,
            company.id,
            masters.PartnerInput(
                name=SUPPLIER_NAME,
                supplier_code=SUPPLIER_CODE,
                tin="102345678",
                email="ap@musanze-packaging.example",
                phone="+250788000111",
            ),
            actor=actor,
        )
        terms = {row.code: row for row in masters.list_payment_terms(db, company.id)}
        masters.upsert_role_settings(
            db,
            company.id,
            supplier,
            PartnerRole.AP,
            masters.RoleSettingsInput(
                payment_terms_id=terms["NET30"].id if "NET30" in terms else None,
                tax_mode=TaxMode.EXCLUSIVE,
            ),
            actor=actor,
        )
        db.commit()
    return supplier.supplier_code


def _ensure_usd_bank_account(db, *, company: Company, actor: User) -> str:
    """P8 decision 9: Rugari Wines E2E holds a second bank account, `1121 Bank Account USD`,
    beside `1120`; Kivu Traders holds only the seeded pair. One company with a foreign-currency
    bank account and one without, so every banking screen is exercised both ways.

    Through `register` with a new GL account — the call the Bank accounts screen's *New* makes
    — so the chart row and its master are written together, and the master is in USD from its
    first moment rather than registered in RWF and moved. Deferred here from P8 step 1, which
    had no screen that read it.
    """
    from app.banking import accounts as bank_accounts
    from app.db import set_tenant
    from app.models.banking import BankAccountKind
    from app.models.currency import Currency
    from app.models.gl import GLAccount

    set_tenant(db, company.id)
    existing = db.scalar(
        select(GLAccount).where(
            GLAccount.company_id == company.id, GLAccount.code == USD_BANK_CODE
        )
    )
    if existing is None:
        parent = db.scalar(
            select(GLAccount).where(GLAccount.company_id == company.id, GLAccount.code == "1100")
        )
        usd = db.scalar(
            select(Currency).where(Currency.company_id == company.id, Currency.code == "USD")
        )
        bank_accounts.register(
            db,
            company.id,
            bank_accounts.BankAccountInput(
                new_account=bank_accounts.NewGLAccount(
                    code=USD_BANK_CODE,
                    name=USD_BANK_NAME,
                    kind=BankAccountKind.BANK,
                    parent_id=parent.id if parent is not None else None,
                ),
                currency_id=usd.id,
                bank_name="Bank of Kigali",
                account_number="00040-0000999-11",
                account_holder=PRIMARY_COMPANY,
            ),
            actor=actor,
        )
        db.commit()
    return USD_BANK_CODE


def _ensure_open_period(db, *, company: Company, on: date) -> None:
    """The aged invoices are dated months back, which can fall outside the fiscal year the
    company was provisioned with (`seed_fiscal_year` creates the calendar year it was signed
    up in, and nothing before it). Create the missing year, and open the period if it is
    sitting in `future` — the periods after today are seeded `future`, and a fixture dated
    into one of those would otherwise be unpostable."""
    from app.db import set_tenant
    from app.kernel.periods import create_fiscal_year

    set_tenant(db, company.id)
    period = db.scalar(
        select(AccountingPeriod).where(
            AccountingPeriod.company_id == company.id,
            AccountingPeriod.start_date <= on,
            AccountingPeriod.end_date >= on,
        )
    )
    if period is None:
        create_fiscal_year(
            db,
            company.id,
            name=str(on.year),
            start_date=date(on.year, 1, 1),
            end_date=date(on.year, 12, 31),
            open_through=date.today(),
        )
        db.commit()
        return
    if period.status != PeriodStatus.OPEN:
        period.status = PeriodStatus.OPEN
        db.commit()


def _ensure_aged_invoices(db, *, company: Company, actor: User) -> str | None:
    """A customer with two invoices already overdue, posted through `post_document` with a
    real actor — the same path the AR invoice screen takes, so the open items, the control
    account and the ageing all see exactly what the application would have written."""
    from app.db import set_tenant
    from app.models.gl import GLAccount
    from app.models.partner import PartnerRole
    from app.models.subledger import DocumentKind
    from app.subledger import documents as documents_service
    from app.subledger import masters

    set_tenant(db, company.id)
    existing = masters.list_partners(db, company.id, role=PartnerRole.AR, include_inactive=True)
    customer = next((p for p in existing if p.customer_code == AGED_CUSTOMER_CODE), None)
    if customer is not None:
        return customer.customer_code  # already seeded — the invoices went in with it

    revenue = db.scalar(
        select(GLAccount).where(
            GLAccount.company_id == company.id, GLAccount.code == AGED_REVENUE_ACCOUNT
        )
    )
    if revenue is None:
        raise RuntimeError(
            f"revenue account {AGED_REVENUE_ACCOUNT} missing for company {company.id}"
        )

    customer = masters.create_partner(
        db,
        company.id,
        masters.PartnerInput(
            name=AGED_CUSTOMER_NAME,
            customer_code=AGED_CUSTOMER_CODE,
            tin="103456789",
            email="accounts@gisenyi-hotels.example",
            phone="+250788000222",
        ),
        actor=actor,
    )
    db.commit()

    today = date.today()
    for doc_days, due_days, amount, description in AGED_INVOICES:
        document_date = today - timedelta(days=doc_days)
        _ensure_open_period(db, company=company, on=document_date)
        set_tenant(db, company.id)
        documents_service.post_document(
            db,
            company.id,
            PartnerRole.AR,
            documents_service.DocumentInput(
                kind=DocumentKind.INVOICE,
                partner_id=customer.id,
                document_date=document_date,
                due_date=today - timedelta(days=due_days),
                description=description,
                lines=(
                    documents_service.LineInput(
                        unit_price=amount,
                        gl_account_id=revenue.id,
                        description=description,
                    ),
                ),
            ),
            actor=actor,
        )
        db.commit()
    return customer.customer_code


def _ensure_closed_period(db, *, company: Company) -> str | None:
    """The earliest period that none of the aged invoices needs. Closing the first period
    unconditionally would, whenever `today - 100 days` lands in it, close the period one of
    those invoices posts into and the fixture could not seed itself."""
    aged = _aged_document_dates(date.today())
    with platform_scope(db):
        periods = db.scalars(
            select(AccountingPeriod)
            .where(AccountingPeriod.company_id == company.id)
            .order_by(AccountingPeriod.period_no)
        ).all()
        period = next(
            (
                candidate
                for candidate in periods
                if not any(candidate.start_date <= day <= candidate.end_date for day in aged)
            ),
            None,
        )
        if period is None:
            return None
        if period.status != PeriodStatus.CLOSED:
            period.status = PeriodStatus.CLOSED
            db.commit()
        return period.name


def main() -> None:
    # Before the session: an unset credential should stop the script, not open a connection
    # and then stop it.
    password = fixture_password()
    db = SessionLocal()
    try:
        primary_user, primary_company = _get_or_create_tenant(
            db,
            company_name=PRIMARY_COMPANY,
            email=PRIMARY_EMAIL,
            full_name="E2E Primary Owner",
            password=password,
        )
        secondary_user, secondary_company = _get_or_create_tenant(
            db,
            company_name=SECONDARY_COMPANY,
            email=SECONDARY_EMAIL,
            full_name="E2E Secondary Owner",
            password=password,
        )
        _ensure_cross_company_membership(db, user=secondary_user, company=primary_company)
        _ensure_member_with_role(
            db,
            company=primary_company,
            email=READONLY_EMAIL,
            full_name="E2E Read Only",
            role_name=READONLY_ROLE_NAME,
            password=password,
        )
        _ensure_member_with_role(
            db,
            company=primary_company,
            email=POSTER_EMAIL,
            full_name="E2E Poster",
            role_name=POSTER_ROLE_NAME,
            password=password,
        )
        supplier_code = _ensure_partners(db, company=primary_company, actor=primary_user)
        usd_bank_code = _ensure_usd_bank_account(db, company=primary_company, actor=primary_user)
        aged_customer_code = _ensure_aged_invoices(
            db, company=primary_company, actor=primary_user
        )
        closed_period = _ensure_closed_period(db, company=primary_company)

        print(
            json.dumps(
                {
                    "primary_email": PRIMARY_EMAIL,
                    "primary_company": PRIMARY_COMPANY,
                    "secondary_email": SECONDARY_EMAIL,
                    "secondary_company": SECONDARY_COMPANY,
                    "readonly_email": READONLY_EMAIL,
                    "readonly_role": READONLY_ROLE_NAME,
                    "poster_email": POSTER_EMAIL,
                    "poster_role": POSTER_ROLE_NAME,
                    "supplier_code": supplier_code,
                    "usd_bank_code": usd_bank_code,
                    "aged_customer_code": aged_customer_code,
                    # **Which of the two sources it came from**, never the value itself: the
                    # reader already has that wherever it lives, and a credential in a log is a
                    # credential in a CI artefact. Saying which one is what makes a wrong
                    # password diagnosable — "it read .env" when you thought you had exported
                    # one is the whole of step 8's drift, in a line.
                    "password_from": password_source(),
                    "closed_period": closed_period,
                },
                indent=2,
            )
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
