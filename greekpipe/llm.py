"""OpenRouter chat client: the only module that talks to the LLM API."""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import httpx

REFERER = "https://github.com/risedphantom/greek"
TITLE = "greek-course-companion"


class LLMError(Exception):
    pass


class LLMTransient(LLMError):
    """Rate limit, 5xx, timeout or transport failure that persisted past all retries."""


class LLMBadOutput(LLMError):
    """The API replied, but not with a usable JSON object."""


def image_part(path: Path) -> dict:
    data = base64.b64encode(Path(path).read_bytes()).decode()
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{data}"}}


def text_part(s: str) -> dict:
    return {"type": "text", "text": s}


def _strip_fences(content: str) -> str:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _retry_after(resp: httpx.Response) -> int:
    try:
        return int(resp.headers.get("Retry-After", 0))
    except ValueError:
        return 0


class LLMClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://openrouter.ai/api/v1",
        http: httpx.Client | None = None,
        max_retries: int = 4,
        sleep=time.sleep,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.http = http or httpx.Client(timeout=120)
        self.max_retries = max_retries
        self.sleep = sleep
        self.last_cost = 0.0
        self.last_usage: dict = {}

    def chat_json(self, model: str, system: str, user_parts: list[dict], schema_name: str) -> dict:
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_parts},
            ],
            "response_format": {"type": "json_object"},
        }
        data = self._post_with_retry(body, schema_name)
        usage = data.get("usage") or {}
        self.last_usage = usage
        self.last_cost += float(usage.get("cost") or 0.0)
        try:
            content = data["choices"][0]["message"]["content"]
            parsed = json.loads(_strip_fences(content))
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
            raise LLMBadOutput(f"{schema_name}: reply is not valid JSON ({e})") from e
        if not isinstance(parsed, dict):
            raise LLMBadOutput(f"{schema_name}: reply is not a JSON object")
        return parsed

    def _post_with_retry(self, body: dict, schema_name: str) -> dict:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": REFERER,
            "X-Title": TITLE,
        }
        reason = ""
        for attempt in range(self.max_retries + 1):
            retry_after = 0
            try:
                resp = self.http.post(f"{self.base_url}/chat/completions", headers=headers, json=body)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                reason = f"{type(e).__name__}: {e}"
            else:
                if resp.status_code == 429 or resp.status_code >= 500:
                    reason = f"HTTP {resp.status_code}"
                    retry_after = _retry_after(resp)
                elif resp.status_code >= 400:
                    raise LLMBadOutput(f"{schema_name}: HTTP {resp.status_code}: {resp.text[:200]}")
                else:
                    data = resp.json()
                    err = data.get("error")
                    if not err:
                        return data
                    code = err.get("code")
                    if not (isinstance(code, int) and (code == 429 or code >= 500)):
                        raise LLMBadOutput(f"{schema_name}: API error {code}: {err.get('message')}")
                    reason = f"API error {code}"
            if attempt < self.max_retries:
                self.sleep(max(2 ** (attempt + 1), retry_after))
        raise LLMTransient(f"{schema_name}: gave up after {self.max_retries + 1} attempts ({reason})")
