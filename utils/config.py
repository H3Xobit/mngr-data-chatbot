"""Application configuration via environment variables."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    groq_api_key: str = Field(default="", alias="GROQ_API_KEY")
    groq_model: str = Field(default="llama-3.3-70b-versatile", alias="GROQ_MODEL")

    session_secret: str = Field(
        default="dev-only-please-change", alias="SESSION_SECRET"
    )
    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=8500, alias="PORT")

    ecommerce_db: str = Field(default="ecommerce.db", alias="ECOMMERCE_DB")
    support_db: str = Field(default="support.db", alias="SUPPORT_DB")

    project_root: Path = PROJECT_ROOT

    @property
    def ecommerce_db_path(self) -> Path:
        return (self.project_root / self.ecommerce_db).resolve()

    @property
    def support_db_path(self) -> Path:
        return (self.project_root / self.support_db).resolve()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    (PROJECT_ROOT / "logs").mkdir(parents=True, exist_ok=True)
    return s
