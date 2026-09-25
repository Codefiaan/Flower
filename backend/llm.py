"""Provider-agnostic LLM client (bring your own key).

Most providers speak the OpenAI "chat completions" protocol (DeepSeek, OpenAI, OpenRouter,
Groq, Gemini's OpenAI endpoint, local Ollama). Anthropic uses its own Messages API.
Model names change often, so every preset's model is only a suggestion you can override.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterator

import httpx

from . import db

PRESETS = {
    "deepseek": {"label": "DeepSeek", "base_url": "https://api.deepseek.com", "model": "deepseek-chat", "protocol": "openai"},
    "openai": {"label": "OpenAI", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini", "protocol": "openai"},
    "anthropic": {"label": "Anthropic (Claude)", "base_url": "https://api.anthropic.com", "model": "claude-sonnet-5", "protocol": "anthropic"},
    "gemini": {"label": "Google Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "model": "gemini-2.5-flash", "protocol": "openai"},
    "openrouter": {"label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "model": "deepseek/deepseek-chat", "protocol": "openai"},
    "groq": {"label": "Groq", "base_url": "https://api.groq.com/openai/v1", "model": "llama-3.3-70b-versatile", "protocol": "openai"},
    "ollama": {"label": "Ollama (local, no key)", "base_url": "http://localhost:11434/v1", "model": "llama3.1", "protocol": "openai"},
    "custom": {"label": "Other OpenAI-compatible", "base_url": "", "model": "", "protocol": "openai"},
}


class LLMError(RuntimeError):
    pass


@dataclass
class LLMConfig:
    provider: str
    api_key: str
    model: str
    base_url: str
    protocol: str

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.model and (self.api_key or self.provider == "ollama"))


def load_config() -> LLMConfig:
    provider = db.get_setting("llm_provider", "deepseek")
    preset = PRESETS.get(provider, PRESETS["custom"])
    return LLMConfig(
        provider=provider,
        api_key=db.get_setting("llm_api_key", ""),
        model=db.get_setting("llm_model", "") or preset["model"],
        base_url=(db.get_setting("llm_base_url", "") or preset["base_url"]).rstrip("/"),
        protocol=preset["protocol"],
    )


def _sse_lines(resp: httpx.Response) -> Iterator[dict]:
    for line in resp.iter_lines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            return
        try:
            yield json.loads(payload)
        except json.JSONDecodeError:
            continue


def _raise_for(resp: httpx.Response) -> None:
    if resp.status_code >= 400:
        resp.read()
        raise LLMError(f"LLM provider returned HTTP {resp.status_code}: {resp.text[:300]}")


def stream_chat(messages: list[dict], system: str, cfg: LLMConfig | None = None,
                max_tokens: int = 4000, timeout: float = 180) -> Iterator[str]:
    """Yield text chunks of the model's answer."""
    cfg = cfg or load_config()
    if not cfg.configured:
        raise LLMError("No LLM configured. Add a provider and API key under Settings.")
    if cfg.protocol == "anthropic":
        url = f"{cfg.base_url}/v1/messages"
        headers = {"x-api-key": cfg.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        body = {"model": cfg.model, "system": system, "messages": messages, "max_tokens": max_tokens, "stream": True}
        with httpx.stream("POST", url, headers=headers, json=body, timeout=timeout) as resp:
            _raise_for(resp)
            for ev in _sse_lines(resp):
                if ev.get("type") == "content_block_delta" and ev.get("delta", {}).get("type") == "text_delta":
                    yield ev["delta"]["text"]
                elif ev.get("type") == "error":
                    raise LLMError(str(ev.get("error")))
        return
    url = f"{cfg.base_url}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
    body = {"model": cfg.model, "messages": [{"role": "system", "content": system}, *messages],
            "max_tokens": max_tokens, "stream": True, "temperature": 0.2}
    with httpx.stream("POST", url, headers=headers, json=body, timeout=timeout) as resp:
        _raise_for(resp)
        for ev in _sse_lines(resp):
            for choice in ev.get("choices", []):
                text = (choice.get("delta") or {}).get("content")
                if text:
                    yield text


def test_connection(cfg: LLMConfig | None = None) -> str:
    return "".join(stream_chat([{"role": "user", "content": "Reply with the single word: OK"}],
                               "You are a connectivity test.", cfg, max_tokens=10, timeout=30)).strip()
