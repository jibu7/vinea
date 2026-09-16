from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_JWT_SECRET = "dev-only-insecure-secret-change-me"
#: A fixed, published Fernet key. Development and the test suite encrypt device keys under it
#: so that the encryption path is the one that runs everywhere rather than a branch nobody
#: exercises; production refuses to boot without a real one, exactly as it does for the JWT
#: secret. Published on purpose: a key in the repository is not a secret, and pretending
#: otherwise is how a dev default reaches production unnoticed.
#: base64 of the 32 bytes `dev-only-fiscal-key-secret-vinea`.
DEV_FISCAL_KEY_SECRET = "ZGV2LW9ubHktZmlzY2FsLWtleS1zZWNyZXQtdmluZWE="


class Settings(BaseSettings):
    app_env: str = "dev"
    app_name: str = "Vinea ERP"
    app_version: str = "0.1.0"
    # Runtime connects as non-superuser vinea_app (NOBYPASSRLS) to enforce tenant isolation
    database_url: str = "postgresql+psycopg://vinea_app:vinea_app@localhost:5432/vinea"
    # Migrations run under superuser role
    migration_database_url: str = "postgresql+psycopg://vinea:vinea@localhost:5432/vinea"
    redis_url: str = "redis://localhost:6379/0"
    cors_origins: list[str] = ["http://localhost:3000"]
    frontend_base_url: str = "http://localhost:3000"
    #: Where the non-production mail stub also writes each message, one JSON object per line.
    #: Unset in normal running, and refused outright in production by the validator below.
    #:
    #: It exists for **end-to-end tests**, which are the only reader that cannot use the
    #: in-memory `outbox`: that lives in the uvicorn worker's own memory, and Playwright is a
    #: different process on a different machine. The alternative was an endpoint that returns
    #: a one-time token, which would make the token an API affordance — the thing the whole
    #: mailed-token design exists to avoid. A file the mail stub drops messages into is the
    #: same idea as a local mail catcher, and adds no surface to the product.
    email_outbox_file: str | None = None

    # --- Fiscalization (P7) ---------------------------------------------------------------
    #: Fernet key protecting the three EBM device keys at rest (decision 2). They are the only
    #: secrets this phase holds, and they are held because the authority's own protocol needs
    #: them on every signed call — so the question is not whether to store them but how, and
    #: the answer is encrypted, never returned by an endpoint, never logged, and stripped from
    #: every stored payload.
    fiscal_key_secret: str = DEV_FISCAL_KEY_SECRET
    #: Mounts the in-repo EBM sandbox (decision 16) as a router on this app. It answers as the
    #: revenue authority would, which is precisely why production refuses to start with it
    #: set — the same shape as `email_outbox_file`, and for the same reason: the mistake is
    #: plausible, so it is made impossible.
    fiscal_sandbox_enabled: bool = False

    # Auth — ADR-03
    jwt_secret: str = DEV_JWT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 14
    invitation_ttl_days: int = 7
    password_reset_ttl_hours: int = 2
    email_verification_ttl_hours: int = 48

    cookie_secure: bool = False
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    cookie_domain: str | None = None

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def is_production(self) -> bool:
        return self.app_env in {"prod", "production"}

    @model_validator(mode="after")
    def _harden_production(self) -> "Settings":
        """Every production requirement, checked together.

        **All of them, not the first that fails.** P7 added a second required secret, and
        raising on whichever came first made the message depend on declaration order — a
        deployment missing two things would fix one, redeploy, and be told about the other.
        Collecting them costs nothing and the message names the whole job.
        """
        if not self.is_production:
            return self
        problems: list[str] = []
        if self.jwt_secret == DEV_JWT_SECRET:
            problems.append("JWT_SECRET must be set outside development")
        if self.fiscal_key_secret == DEV_FISCAL_KEY_SECRET:
            # The three EBM device keys are encrypted under it. A published default here means
            # the ciphertext in the database is readable by anybody holding this repository.
            problems.append("FISCAL_KEY_SECRET must be set outside development")
        if self.fiscal_sandbox_enabled:
            # An app serving the sandbox is an app that will answer `saveSales` itself and
            # hand back a receipt nobody filed. Refuse at boot rather than discover it on a
            # VAT return.
            problems.append("FISCAL_SANDBOX_ENABLED must not be set in production")
        if self.email_outbox_file:
            # Writing one-time tokens to a file is a test affordance. Production refuses to
            # start rather than doing it quietly, which is the same shape as the checks above:
            # the mistake is plausible, so it is made impossible.
            problems.append("EMAIL_OUTBOX_FILE must not be set in production")
        if problems:
            raise ValueError("; ".join(problems))
        self.cookie_secure = True
        return self


settings = Settings()
