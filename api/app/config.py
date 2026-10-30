from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # Application
    MCSS_ENV: str = Field(
        default="development", description="Environment: development, test, production"
    )
    MCSS_API_HOST: str = Field(default="0.0.0.0", description="API host")
    MCSS_API_PORT: int = Field(default=8000, description="API port")

    # Database
    MCSS_DATABASE_URL: str = Field(
        default="postgresql+psycopg://mcss:change-me@postgres:5432/mcss",
        description="PostgreSQL connection URL",
    )
    MCSS_REDIS_URL: str = Field(default="redis://redis:6379/0")
    MCSS_CELERY_BROKER_URL: str = Field(default="redis://redis:6379/1")
    MCSS_CELERY_RESULT_BACKEND: str = Field(default="redis://redis:6379/2")
    MCSS_WUKONGIM_API_URL: str = Field(default="http://wukongim:5001")
    MCSS_WUKONGIM_WS_URL: str = Field(default="ws://localhost:5200")
    MCSS_WUKONGIM_SYSTEM_UID: str = Field(default="mcss-system")

    # Authentication
    MCSS_JWT_SECRET: SecretStr = Field(
        default=SecretStr("replace-with-at-least-32-random-characters"),
        description="JWT secret key (min 32 chars)",
    )
    MCSS_JWT_ACCESS_MINUTES: int = Field(default=15, description="Access token lifetime in minutes")
    MCSS_JWT_REFRESH_DAYS: int = Field(default=7, description="Refresh token lifetime in days")

    # CORS
    MCSS_CORS_ORIGINS: str = Field(
        default="http://localhost:5173,http://localhost:5174",
        description="Comma-separated allowed CORS origins",
    )

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.MCSS_CORS_ORIGINS.split(",")]

    @field_validator("MCSS_ENV")
    @classmethod
    def validate_environment(cls, value: str) -> str:
        if value not in {"development", "test", "production"}:
            raise ValueError("MCSS_ENV must be development, test, or production")
        return value

    # Widget
    MCSS_WIDGET_ALLOWED_ORIGIN: str = Field(
        default="http://localhost:5174",
        description="Allowed origin for widget session creation",
    )

    # LLM Provider
    MCSS_LLM_BASE_URL: str = Field(default="fake", description="LLM provider base URL (or 'fake')")
    MCSS_LLM_API_KEY: SecretStr = Field(default=SecretStr(""), description="LLM API key")
    MCSS_LLM_CHAT_MODEL: str = Field(default="", description="LLM chat model name")

    # Embeddings
    MCSS_EMBEDDING_MODEL: str = Field(default="", description="Embedding model name")
    MCSS_EMBEDDING_DIMENSIONS: int = Field(default=1536, description="Embedding vector dimensions")

    # Retrieval
    MCSS_RETRIEVAL_MIN_SCORE: float = Field(
        default=0.72, description="Minimum retrieval cosine score"
    )

    # Admin bootstrap
    MCSS_ADMIN_EMAIL: str = Field(
        default="admin@example.com", description="Admin email for bootstrap"
    )
    MCSS_ADMIN_PASSWORD: SecretStr = Field(
        default=SecretStr(""), description="Admin password for bootstrap"
    )

    @property
    def DATABASE_URL(self) -> str:
        return self.MCSS_DATABASE_URL

    @property
    def JWT_SECRET(self) -> str:
        return self.MCSS_JWT_SECRET.get_secret_value()

    @property
    def JWT_ACCESS_MINUTES(self) -> int:
        return self.MCSS_JWT_ACCESS_MINUTES

    @property
    def JWT_REFRESH_DAYS(self) -> int:
        return self.MCSS_JWT_REFRESH_DAYS

    @property
    def WIDGET_ALLOWED_ORIGIN(self) -> str:
        return self.MCSS_WIDGET_ALLOWED_ORIGIN

    @property
    def LLM_BASE_URL(self) -> str:
        return self.MCSS_LLM_BASE_URL

    @property
    def LLM_API_KEY(self) -> str:
        return self.MCSS_LLM_API_KEY.get_secret_value()

    @property
    def LLM_CHAT_MODEL(self) -> str:
        return self.MCSS_LLM_CHAT_MODEL

    @property
    def EMBEDDING_MODEL(self) -> str:
        return self.MCSS_EMBEDDING_MODEL

    @property
    def EMBEDDING_DIMENSIONS(self) -> int:
        return self.MCSS_EMBEDDING_DIMENSIONS

    @property
    def RETRIEVAL_MIN_SCORE(self) -> float:
        return self.MCSS_RETRIEVAL_MIN_SCORE

    @property
    def ADMIN_EMAIL(self) -> str:
        return self.MCSS_ADMIN_EMAIL

    @property
    def ADMIN_PASSWORD(self) -> str:
        return self.MCSS_ADMIN_PASSWORD.get_secret_value()

    @property
    def is_test_or_fake(self) -> bool:
        return self.MCSS_ENV == "test" or self.MCSS_LLM_BASE_URL == "fake"

    def validate_production(self) -> None:
        if self.MCSS_ENV == "production":
            if len(self.JWT_SECRET) < 32:
                raise ValueError("MCSS_JWT_SECRET must be at least 32 characters in production")
            if "*" in self.MCSS_CORS_ORIGINS:
                raise ValueError("Wildcard CORS origins not allowed in production")
            if self.MCSS_LLM_BASE_URL == "fake":
                raise ValueError("MCSS_LLM_BASE_URL cannot be 'fake' in production")
        if self.MCSS_EMBEDDING_DIMENSIONS != 1536:
            raise ValueError("MCSS_EMBEDDING_DIMENSIONS must be 1536 for the initial schema")


settings = Settings()
