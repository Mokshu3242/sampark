import logging
import time
import requests

from app.core.config import settings

log = logging.getLogger("flood-watch.llm")

# Errors where retrying the same provider won't help
PERMANENT_HINTS = ("daily", "quota", "404", "403", "not_found", "suspended", "empty answer")


class LLMError(RuntimeError):
    pass


class Provider:
    name = "base"

    def chat(self, messages: list[dict], temperature: float, max_tokens: int) -> str:
        raise NotImplementedError


class OpenAIStyle(Provider):
    """Works for Groq and Gemini, which both offer an OpenAI-compatible endpoint."""

    def __init__(self, name: str, url: str, key: str, model: str):
        self.name = name
        self.url = url
        self.key = key
        self.model = model

    def chat(self, messages: list[dict], temperature: float, max_tokens: int) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        # Reasoning models: keep thinking short so the answer isn't cut off
        if "gpt-oss" in self.model:
            payload["reasoning_effort"] = "low"

        try:
            resp = requests.post(
                self.url,
                headers={"Authorization": f"Bearer {self.key}"},
                json=payload,
                timeout=60,
            )
        except requests.RequestException as e:
            raise LLMError(f"{self.name} network error: {e}") from e

        if resp.status_code != 200:
            raise LLMError(f"{self.name} HTTP {resp.status_code}: {resp.text[:300]}")

        try:
            content = resp.json()["choices"][0]["message"].get("content") or ""
        except (ValueError, KeyError, IndexError) as e:
            raise LLMError(f"{self.name} bad response shape: {e}") from e

        if not content.strip():
            raise LLMError(f"{self.name} returned an empty answer")
        return content


class Cloudflare(Provider):
    name = "cloudflare"

    def __init__(self, account_id: str, token: str, model: str):
        self.url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"
        self.token = token

    def chat(self, messages: list[dict], temperature: float, max_tokens: int) -> str:
        try:
            resp = requests.post(
                self.url,
                headers={"Authorization": f"Bearer {self.token}"},
                json={"messages": messages, "temperature": temperature, "max_tokens": max_tokens},
                timeout=60,
            )
            data = resp.json()
        except (requests.RequestException, ValueError) as e:
            raise LLMError(f"cloudflare error: {e}") from e

        if resp.status_code != 200 or not data.get("success", False):
            raise LLMError(f"cloudflare HTTP {resp.status_code}: {data.get('errors')}")

        content = (data.get("result") or {}).get("response") or ""
        if not content.strip():
            raise LLMError("cloudflare returned an empty answer")
        return content


def _build(name: str) -> Provider:
    s = settings
    if name == "groq":
        return OpenAIStyle(
            "groq",
            "https://api.groq.com/openai/v1/chat/completions",
            s.groq_api_key,
            s.groq_model,
        )
    if name == "cloudflare":
        return Cloudflare(s.cf_account_id, s.cf_auth_token, s.cf_model)
    if name == "gemini":
        return OpenAIStyle(
            "gemini",
            "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            s.gemini_api_key,
            s.gemini_model,
        )
    raise ValueError(f"Unknown provider: {name}")


class LLM:
    def __init__(self):
        self.providers = [_build(n) for n in settings.available_llms()]
        if not self.providers:
            raise LLMError("No LLM provider has a key in .env")
        self.last_provider: str | None = None

    def chat(self, messages: list[dict], temperature: float = 0.2, max_tokens: int = 1024) -> str:
        errors = []
        for p in self.providers:
            for attempt in range(2):
                try:
                    reply = p.chat(messages, temperature, max_tokens)
                    self.last_provider = p.name
                    return reply
                except LLMError as e:
                    msg = str(e)
                    log.warning(f"{p.name} attempt {attempt + 1} failed: {msg[:200]}")
                    errors.append(msg[:300])
                    if any(h in msg.lower() for h in PERMANENT_HINTS):
                        break  # move on to the next provider
                    time.sleep(1.5)
        raise LLMError("All LLM providers failed:\n" + "\n".join(errors))


def get_llm() -> LLM:
    return LLM()