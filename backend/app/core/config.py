import os
from typing import List, Optional
from pydantic_settings import BaseSettings
from pydantic import Field

class Settings(BaseSettings):
    PROJECT_NAME: str = "Generic AI Business Opportunity Discovery & Sales Platform"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    ENVIRONMENT: str = Field(default="development", validation_alias="ENVIRONMENT")

    # Database: PostgreSQL for production, SQLite for local dev/testing
    DATABASE_URL: str = Field(
        default="sqlite:///./opportunity_platform.db",
        validation_alias="DATABASE_URL"
    )

    # Redis URL for production task queue (RQWorkerPool)
    REDIS_URL: str = Field(default="redis://localhost:6379/0", validation_alias="REDIS_URL")

    # JWT Authentication & RBAC
    JWT_SECRET_KEY: str = Field(default="super-secret-default-jwt-key-for-dev-only-32bytes!", validation_alias="JWT_SECRET_KEY")
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 hours

    # Envelope Encryption (AES-256 KEK)
    # Master Key Encryption Key for provider credentials & API keys
    ENCRYPTION_MASTER_KEY: str = Field(
        default="MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",  # 32-byte base64 dev fallback
        validation_alias="ENCRYPTION_MASTER_KEY"
    )

    # Key-Versioned Cryptographic Suppression HMAC Keys
    SUPPRESSION_ACTIVE_KEY_VERSION: int = Field(default=1, validation_alias="SUPPRESSION_ACTIVE_KEY_VERSION")
    SUPPRESSION_HMAC_KEY_V1: str = Field(
        default="default-hmac-key-v1-for-local-development-strictly-isolated!",
        validation_alias="SUPPRESSION_HMAC_KEY_V1"
    )
    SUPPRESSION_HMAC_KEY_V2: Optional[str] = Field(default=None, validation_alias="SUPPRESSION_HMAC_KEY_V2")

    # Global Emergency Kill Switch
    GLOBAL_EMERGENCY_KILL_SWITCH: bool = Field(default=False, validation_alias="GLOBAL_EMERGENCY_KILL_SWITCH")

    # Unsubscribe Token Secret & Salt
    UNSUBSCRIBE_TOKEN_SECRET: str = Field(
        default="unsubscribe-token-secret-for-dev-32bytes-entropy!",
        validation_alias="UNSUBSCRIBE_TOKEN_SECRET"
    )
    UNSUBSCRIBE_TOKEN_EXPIRE_DAYS: int = 30

    # CSRF Secret for Browser Unsubscribe Confirmation
    CSRF_SECRET_KEY: str = Field(
        default="csrf-protection-secret-key-32bytes-minimum-entropy!",
        validation_alias="CSRF_SECRET_KEY"
    )

    # Queue & Worker Settings
    QUEUE_MODE: str = Field(default="auto", validation_alias="QUEUE_MODE")  # "auto", "redis", "sync"

    # Inbound Webhook Secret for HMAC verification
    INBOUND_WEBHOOK_SECRET: str = Field(
        default="whsec_inbound_development_secret_32bytes!",
        validation_alias="INBOUND_WEBHOOK_SECRET"
    )

    # Email Transport Mode
    EMAIL_TRANSPORT_MODE: str = Field(default="auto", validation_alias="EMAIL_TRANSPORT_MODE")

    # SMTP Credentials (for SMTP transport)
    SMTP_HOST: Optional[str] = Field(default=None, validation_alias="SMTP_HOST")
    SMTP_PORT: int = Field(default=587, validation_alias="SMTP_PORT")
    SMTP_USERNAME: Optional[str] = Field(default=None, validation_alias="SMTP_USERNAME")
    SMTP_PASSWORD: Optional[str] = Field(default=None, validation_alias="SMTP_PASSWORD")
    SMTP_USE_TLS: bool = Field(default=True, validation_alias="SMTP_USE_TLS")

    # Transactional Email API (Resend / Postmark)
    EMAIL_API_PROVIDER: Optional[str] = Field(default=None, validation_alias="EMAIL_API_PROVIDER")  # "resend" or "postmark"
    EMAIL_API_KEY: Optional[str] = Field(default=None, validation_alias="EMAIL_API_KEY")

    # LLM Provider Configuration (Model-Agnostic Interface)
    LLM_PROVIDER: str = Field(default="openai", validation_alias="LLM_PROVIDER")  # "openai", "gemini", "groq", "rule_based", "mock"
    LLM_FALLBACK_PROVIDER: Optional[str] = Field(default=None, validation_alias="LLM_FALLBACK_PROVIDER")
    OPENAI_API_KEY: Optional[str] = Field(default=None, validation_alias="OPENAI_API_KEY")
    OPENAI_MODEL: str = Field(default="gpt-4o-mini", validation_alias="OPENAI_MODEL")
    OPENAI_BASE_URL: str = Field(default="https://api.openai.com/v1", validation_alias="OPENAI_BASE_URL")
    GEMINI_API_KEY: Optional[str] = Field(default=None, validation_alias="GEMINI_API_KEY")
    GEMINI_MODEL: str = Field(default="gemini-3.8-flash", validation_alias="GEMINI_MODEL")
    GEMINI_BASE_URL: str = Field(default="https://generativelanguage.googleapis.com/v1beta/openai", validation_alias="GEMINI_BASE_URL")
    GROQ_API_KEY: Optional[str] = Field(default=None, validation_alias="GROQ_API_KEY")
    GROQ_MODEL: str = Field(default="openai/gpt-oss-120b", validation_alias="GROQ_MODEL")
    GROQ_BASE_URL: str = Field(default="https://api.groq.com/openai/v1", validation_alias="GROQ_BASE_URL")
    LLM_API_KEY: Optional[str] = Field(default=None, validation_alias="LLM_API_KEY")
    LLM_API_BASE_URL: Optional[str] = Field(default=None, validation_alias="LLM_API_BASE_URL")
    LLM_MODEL_NAME: str = Field(default="gpt-4o-mini", validation_alias="LLM_MODEL_NAME")
    LLM_TIMEOUT_SECONDS: float = Field(default=30.0, validation_alias="LLM_TIMEOUT_SECONDS")
    LLM_MAX_RETRIES: int = Field(default=3, validation_alias="LLM_MAX_RETRIES")
    LLM_MAX_INPUT_TOKENS: int = Field(default=8000, validation_alias="LLM_MAX_INPUT_TOKENS")
    LLM_MAX_OUTPUT_TOKENS: int = Field(default=2000, validation_alias="LLM_MAX_OUTPUT_TOKENS")
    LLM_MAX_COST_PER_RUN: float = Field(default=0.50, validation_alias="LLM_MAX_COST_PER_RUN")
    LLM_DAILY_COST_LIMIT: float = Field(default=10.00, validation_alias="LLM_DAILY_COST_LIMIT")

    # CORS
    BACKEND_CORS_ORIGINS: List[str] = [
        "http://localhost:5173",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
        "http://localhost:5500",
        "http://127.0.0.1:5500",
        "http://localhost:8000",
        "http://127.0.0.1:8000"
    ]

    _BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _ROOT_DIR = os.path.dirname(_BACKEND_DIR)

    model_config = {
        "env_file": [
            os.path.join(_BACKEND_DIR, ".env"),
            os.path.join(_ROOT_DIR, ".env"),
            ".env",
            "backend/.env",
            "../.env",
        ],
        "case_sensitive": True,
        "extra": "ignore"
    }

    def validate_production_readiness(self):
        """Strict validation for production deployments."""
        if self.ENVIRONMENT.lower() in ["production", "prod"]:
            if not (self.DATABASE_URL.startswith("postgresql://") or self.DATABASE_URL.startswith("postgres://")):
                raise ValueError(
                    "Production Deployment Configuration Error: DATABASE_URL must be a PostgreSQL connection string "
                    "(postgresql://user:password@host:port/dbname). SQLite is strictly forbidden in production."
                )
            if "super-secret-default" in self.JWT_SECRET_KEY:
                raise ValueError("Production Configuration Error: Default JWT_SECRET_KEY is insecure for production.")
            if self.ENCRYPTION_MASTER_KEY == "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=":
                raise ValueError("Production Configuration Error: Default ENCRYPTION_MASTER_KEY is insecure for production.")
            if "default-hmac-key" in self.SUPPRESSION_HMAC_KEY_V1:
                raise ValueError("Production Configuration Error: Default SUPPRESSION_HMAC_KEY_V1 is insecure for production.")
            if not self.REDIS_URL or not self.REDIS_URL.startswith("redis"):
                raise ValueError("Production Configuration Error: REDIS_URL must point to a valid Redis/Valkey instance in production.")
            if self.QUEUE_MODE == "sync":
                raise ValueError("Production Configuration Error: QUEUE_MODE cannot be 'sync' in production. Redis/RQ queue is required.")

settings = Settings()
settings.validate_production_readiness()
