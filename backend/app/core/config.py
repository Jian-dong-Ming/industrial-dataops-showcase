import warnings
from pathlib import Path
from typing import Literal, Self

from pydantic import (
    EmailStr,
    Field,
    HttpUrl,
    PostgresDsn,
    computed_field,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Use top level .env file (one level above ./backend/)
        env_file=("../.env", "../.env.ai"),
        env_ignore_empty=True,
        extra="ignore",
    )
    API_V1_STR: str = "/api/v1"
    SECRET_KEY: str
    # 60 minutes * 24 hours * 8 days = 8 days
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 8
    FRONTEND_HOST: str = "http://localhost:5173"
    FASTAPI_ENV: Literal["development"] | None = None

    PROJECT_NAME: str
    SENTRY_DSN: HttpUrl | None = None
    DATABASE_URL: PostgresDsn

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def _use_psycopg_driver(cls, value: str | PostgresDsn) -> str:
        database_url = str(value)
        for scheme in ("postgres://", "postgresql://"):
            if database_url.startswith(scheme):
                return database_url.replace(scheme, "postgresql+psycopg://", 1)
        return database_url

    SMTP_TLS: bool = True
    SMTP_SSL: bool = False
    SMTP_PORT: int = 587
    SMTP_HOST: str | None = None
    SMTP_LOCAL_HOSTNAME: str = "localhost"
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    EMAILS_FROM_EMAIL: EmailStr | None = None
    EMAILS_FROM_NAME: str | None = None

    @model_validator(mode="after")
    def _set_default_emails_from(self) -> Self:
        if not self.EMAILS_FROM_NAME:
            self.EMAILS_FROM_NAME = self.PROJECT_NAME
        return self

    EMAIL_RESET_TOKEN_EXPIRE_HOURS: int = 48

    @computed_field  # type: ignore[prop-decorator]
    @property
    def emails_enabled(self) -> bool:
        return bool(self.SMTP_HOST and self.EMAILS_FROM_EMAIL)

    EMAIL_TEST_USER: EmailStr = "test@example.com"
    FIRST_SUPERUSER: EmailStr
    FIRST_SUPERUSER_PASSWORD: str

    OPCUA_ALLOWED_HOSTS: str = "opcua-simulator,localhost,127.0.0.1"
    OPCUA_CLIENT_TIMEOUT_SECONDS: float = 5.0
    OPCUA_WORKER_POLL_SECONDS: float = 1.0
    OPCUA_WORKER_HEARTBEAT_SECONDS: float = 5.0
    OPCUA_SAMPLE_QUEUE_SIZE: int = 10_000

    IMPORT_STORAGE_DIR: Path = Path("/data/imports")
    IMPORT_MAX_FILE_SIZE_BYTES: int = 50 * 1024 * 1024
    IMPORT_CHUNK_SIZE: int = 1_000
    IMPORT_MAX_ISSUES: int = 10_000
    IMPORT_DEFAULT_TIMEZONE: str = "Asia/Shanghai"
    IMPORT_JOB_RECOVERY_SECONDS: int = Field(default=60, ge=10, le=600)
    IMPORT_JOB_MAX_ATTEMPTS: int = Field(default=3, ge=1, le=10)

    # Keys are supplied by the server environment, never by the browser.
    DEEPSEEK_API_KEY: str = Field(default="", repr=False)
    DEEPSEEK_MODEL: str = "deepseek-v4-flash"
    AI_TIMEOUT_SECONDS: float = Field(default=25.0, ge=1, le=60)
    AI_MAX_CALLS: int = Field(default=4, ge=2, le=6)
    AI_MAX_CONTEXT_CHARS: int = Field(default=24000, ge=4000, le=60000)
    AI_REQUESTS_PER_MINUTE: int = Field(default=6, ge=1, le=30)
    AI_MAX_DOCUMENTS_PER_PLANT: int = 100
    AI_EMBEDDING_URL: str = ""
    AI_EMBEDDING_MODEL: str = ""
    AI_EMBEDDING_API_KEY: str = Field(default="", repr=False)
    # Calibrated only on the bundled small BGE-M3 corpus; re-evaluate for other models/data.
    AI_MIN_SEMANTIC_SCORE: float = Field(default=0.50, ge=0, le=1)

    @property
    def opcua_allowed_hosts(self) -> set[str]:
        return {
            host.strip().lower()
            for host in self.OPCUA_ALLOWED_HOSTS.split(",")
            if host.strip()
        }

    def _check_default_secret(self, var_name: str, value: str | None) -> None:
        if value == "changethis":
            message = (
                f'The value of {var_name} is "changethis", '
                "for security, please change it, at least for deployments."
            )
            if self.FASTAPI_ENV == "development":
                warnings.warn(message, stacklevel=1)
            else:
                raise ValueError(message)

    @model_validator(mode="after")
    def _enforce_non_default_secrets(self) -> Self:
        self._check_default_secret("SECRET_KEY", self.SECRET_KEY)
        for host in self.DATABASE_URL.hosts():
            self._check_default_secret("DATABASE_URL password", host["password"])
        self._check_default_secret(
            "FIRST_SUPERUSER_PASSWORD", self.FIRST_SUPERUSER_PASSWORD
        )

        return self


settings = Settings()  # type: ignore
