from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_JWT_SECRET = "dev-only-insecure-secret-change-me"


class Settings(BaseSettings):
    app_env: str = "dev"
    app_name: str = "Vinea ERP"
    app_version: str = "0.1.0"
    database_url: str = "postgresql+psycopg://vinea:vinea@localhost:5432/vinea"
    redis_url: str = "redis://localhost:6379/0"
    cors_origins: list[str] = ["http://localhost:3000"]
    frontend_base_url: str = "http://localhost:3000"

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
        if self.is_production:
            if self.jwt_secret == DEV_JWT_SECRET:
                raise ValueError("JWT_SECRET must be set outside development")
            self.cookie_secure = True
        return self


settings = Settings()
