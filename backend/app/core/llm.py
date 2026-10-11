import json
import logging
import time

import requests

from app.core.config import settings

log = logging.getLogger("flood-watch.llm")

# Errors where retrying the same provider won't help
PERMANENT_HINTS = ("daily", "quota", "404", "403", "not_found", "suspended", "empty answer",
                   "tool_use_failed", "json_validate_failed")


class LLMError(RuntimeError):
    pass


def _as_text(content) -> str:
    """Providers sometimes return parsed JSON or a list of parts instead of a string."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list) and all(isinstance(p, dict) and "text" in p for p in content):
        return "".join(p["text"] for p in content)
    if isinstance(content, (dict, list)):
        return json.dumps(content, ensure_ascii=False)
    return str(content)


def _failed_generation(resp) -> str:
    """Groq JSON mode puts the model's real text in error.failed_generation."""
    try:
        err = resp.json().get("error") or {}
        return str(err.get("failed_generation") or "")
    except (ValueError, AttributeError):
        return ""


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
        last_err = f"{self.name} returned an empty answer"
        # If the model runs out of room (reasoning models think first), retry once with double the budget
        for budget in (max_tokens, max_tokens * 2):
            payload = {
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": budget,
            }

            if "gpt-oss" in self.model:
                payload["reasoning_effort"] = "low"
                # JSON mode only when the prompt asks for JSON (Groq requires the word "json")
                if any("json" in (m.get("content") or "").lower() for m in messages):
                    payload["response_format"] = {"type": "json_object"}

            try:
                resp = requests.post(self.url, headers={"Authorization": f"Bearer {self.key}"},
                                     json=payload, timeout=90)
            except requests.RequestException as e:
                raise LLMError(f"{self.name} network error: {e}") from e

            if resp.status_code != 200:
                # JSON check failed, but the text is often fine. agent._parse() can rescue it.
                if resp.status_code == 400 and "json_validate_failed" in resp.text:
                    gen = _failed_generation(resp)
                    if gen.strip():
                        log.info(f"{self.name} JSON check failed, using raw text: {gen[:300]}")
                        return gen
                raise LLMError(f"{self.name} HTTP {resp.status_code}: {resp.text[:300]}")

            try:
                choice = resp.json()["choices"][0]
            except (ValueError, KeyError, IndexError) as e:
                raise LLMError(f"{self.name} bad response shape: {e}") from e

            content = _as_text((choice.get("message") or {}).get("content"))
            if content.strip():
                return content

            finish = choice.get("finish_reason")
            last_err = f"{self.name} returned an empty answer (finish: {finish})"
            if finish != "length":
                break   # empty for another reason, more tokens won't help
            log.info(f"{self.name} ran out of tokens at {budget}, retrying with {budget * 2}")
        raise LLMError(last_err)


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

        # Workers AI may return an already-parsed JSON object instead of text
        content = _as_text((data.get("result") or {}).get("response"))
        if not content.strip():
            raise LLMError("cloudflare returned an empty answer")
        return content


def _build(name: str) -> Provider:
    s = settings
    if name == "groq":
        return OpenAIStyle("groq", "https://api.groq.com/openai/v1/chat/completions",
                           s.groq_api_key, s.groq_model)
    if name == "cloudflare":
        return Cloudflare(s.cf_account_id, s.cf_auth_token, s.cf_model)
    if name == "gemini":
        return OpenAIStyle("gemini", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                           s.gemini_api_key, s.gemini_model)
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
                except Exception as e:  # never let a provider bug crash the whole request
                    log.warning(f"{p.name} unexpected error: {e}")
                    errors.append(f"{p.name} unexpected: {str(e)[:200]}")
                    break
        raise LLMError("All LLM providers failed:\n" + "\n".join(errors))


def get_llm() -> LLM:
    return LLM()