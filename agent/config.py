"""LLM provider settings. All providers are used through OpenAI-compatible endpoints, so one code path serves all."""
from __future__ import annotations

import os

try:  # optional
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # pragma: no cover
    pass

PROVIDERS = {
    # Free key: https://aistudio.google.com/apikey  (no credit card)
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "key_env": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
        "models": ["gemini-2.5-flash", "gemini-2.5-flash-lite"],
    },
    # Free key: https://console.groq.com/keys  (no credit card)
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "key_env": ["GROQ_API_KEY"],
        "models": ["llama3-70b-8192", "llama-3.1-70b-versatile", "llama-3.1-8b-instant"],
    },
}


def resolve(provider: str | None = None, api_key: str | None = None, model: str | None = None) -> dict:
    provider = (provider or os.getenv("LLM_PROVIDER") or "gemini").lower()
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider '{provider}'. Choose from {list(PROVIDERS)}")
    p = PROVIDERS[provider]
    key = api_key or os.getenv("LLM_API_KEY") or next((os.getenv(k) for k in p["key_env"] if os.getenv(k)), None)
    override = model or os.getenv("LLM_MODEL")
    models = [m.strip() for m in override.split(",")] if override else list(p["models"])
    return {"provider": provider, "api_key": key, "models": models,
            "base_url": os.getenv("LLM_BASE_URL") or p["base_url"]}
