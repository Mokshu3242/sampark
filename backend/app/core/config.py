import json
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND = Path(__file__).resolve().parents[2]   # backend/
REPO = BACKEND.parent                            # repo root
DEFAULT_ORIGIN = "http://localhost:5173"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO / ".env", BACKEND / ".env"),
        extra="ignore",
    )

    env: str = "dev"
    # Read as plain text so a bad value can never crash startup. Use settings.cors_origins.
    cors_origins_raw: str = Field(DEFAULT_ORIGIN, validation_alias="CORS_ORIGINS")

    cdse_client_id: str = ""
    cdse_client_secret: str = ""

    # LLM order: tried left to right, providers without keys are skipped
    llm_order: str = "groq,cloudflare"

    access_key: str = ""     # ACCESS_KEY. Empty = no lock (local dev)

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

    # Where the heavy pipeline runs: "local" (this machine) or "github" (GitHub Actions)
    pipeline_mode: str = "local"
    gh_token: str = ""                    # fine-grained token, Actions: read and write
    gh_repo: str = "Mokshu3242/sampark"
    gh_workflow: str = "pipeline.yml"
    gh_ref: str = "main"

    runs_dir: Path = BACKEND / "data" / "runs"

    @property
    def cors_origins(self) -> list[str]:
        """Accepts JSON ["a","b"], a comma list a,b, or empty (falls back to localhost)."""
        raw = (self.cors_origins_raw or "").strip()
        if raw.startswith("["):
            try:
                items = [str(x) for x in json.loads(raw)]
            except ValueError:
                items = raw.strip("[]").split(",")
        else:
            items = raw.split(",")
        clean = [x.strip().strip("'\"").rstrip("/") for x in items]
        return [x for x in clean if x] or [DEFAULT_ORIGIN]

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