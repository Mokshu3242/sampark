from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND = Path(__file__).resolve().parents[2]   # backend/
REPO = BACKEND.parent                            # repo root

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO / ".env", BACKEND / ".env"),
        extra="ignore",
    )

    env: str = "dev"
    cors_origins: list[str] = ["http://localhost:5173"]

    cdse_client_id: str = ""
    cdse_client_secret: str = ""
    gemini_api_key: str = ""

    runs_dir: Path = BACKEND / "data" / "runs"

    def missing_keys(self) -> list[str]:
        return [k for k in ("gemini_api_key",) if not getattr(self, k)]

settings = Settings()