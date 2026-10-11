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

    # LLM order: tried left to right, providers without keys are skipped
    llm_order: str = "groq,cloudflare"

    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"

    cf_account_id: str = ""
    cf_auth_token: str = ""
    cf_model: str = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"

    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    mongo_uri: str = ""
    mongo_username: str = ""
    mongo_password: str = ""

    b2_endpoint: str = ""
    b2_key_id: str = ""
    b2_app_key: str = ""
    b2_bucket: str = ""

    runs_dir: Path = BACKEND / "data" / "runs"

    def available_llms(self) -> list[str]:
        has_key = {
            "groq": bool(self.groq_api_key),
            "cloudflare": bool(self.cf_account_id and self.cf_auth_token),
            "gemini": bool(self.gemini_api_key),
        }
        order = [p.strip() for p in self.llm_order.split(",") if p.strip()]
        return [p for p in order if has_key.get(p)]

    def missing_keys(self) -> list[str]:
        return [] if self.available_llms() else ["no LLM provider has a key"]


settings = Settings()