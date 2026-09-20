"""Thin OpenAI-compatible chat client with retry + model fallback (free tiers hit 429s)."""
from __future__ import annotations

import time

from . import config


class LLMError(RuntimeError):
    """Raised for user-presentable LLM problems (bad key, quota exhausted, offline...)."""


class LLMClient:
    def __init__(self, provider: str | None = None, api_key: str | None = None, model: str | None = None):
        cfg = config.resolve(provider, api_key, model)
        if not cfg["api_key"]:
            raise LLMError(
                "No API key found. Get a free key at https://aistudio.google.com/apikey (Gemini) or "
                "https://console.groq.com/keys (Groq) and set GEMINI_API_KEY / GROQ_API_KEY (see .env.example)."
            )
        try:
            import openai
        except ImportError as e:  # pragma: no cover
            raise LLMError("The 'openai' package is missing. Run: pip install -r requirements.txt") from e
        self._openai = openai
        self.client = openai.OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"], timeout=60)
        self.models = cfg["models"]
        self.provider = cfg["provider"]

    def list_models(self) -> list[str]:
        return sorted(m.id for m in self.client.models.list())

    def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        o = self._openai
        last: Exception | None = None
        for model in self.models:
            for attempt in range(3):
                try:
                    resp = self.client.chat.completions.create(
                        model=model, messages=messages, tools=tools, tool_choice="auto", temperature=0.2)
                    return self._to_dict(resp.choices[0].message)
                except (o.AuthenticationError, o.PermissionDeniedError) as e:
                    raise LLMError("The API key was rejected. Check the key and provider.") from e
                except o.RateLimitError as e:          # free-tier quota: back off, then try next model
                    last = e
                    time.sleep(min(3 * 2 ** attempt, 15))
                except (o.APIConnectionError, o.APITimeoutError) as e:
                    last = e
                    time.sleep(2)
                except (o.NotFoundError, o.BadRequestError) as e:   # e.g. retired model name -> next model
                    last = e
                    break
                except o.APIStatusError as e:
                    last = e
                    if e.status_code >= 500:
                        time.sleep(2)
                    else:
                        break
        raise LLMError(f"The language model is unavailable right now (free-tier limit or network). Last error: {last}")

    @staticmethod
    def _to_dict(msg) -> dict:
        out: dict = {"role": "assistant", "content": msg.content}
        if getattr(msg, "tool_calls", None):
            calls = []
            for tc in msg.tool_calls:
                d = {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}}
                extra = (getattr(tc, "model_extra", None) or {}).get("extra_content")
                if extra:                      # Gemini "thought signatures" must be echoed back
                    d["extra_content"] = extra
                calls.append(d)
            out["tool_calls"] = calls
        return out
