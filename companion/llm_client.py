"""Streaming HTTP client for llama-server's OpenAI-compatible API.

Uses only urllib from stdlib — no external HTTP libraries.
"""

import json
import urllib.request
import urllib.error
from typing import Generator

from . import config


def _headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if config.LLM_API_KEY:
        h["Authorization"] = f"Bearer {config.LLM_API_KEY}"
    return h


def health_check() -> bool:
    """Return True if Ollama is reachable."""
    try:
        req = urllib.request.Request(
            f"{config.LLM_BASE_URL}/api/tags", headers=_headers()
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def stream_chat(
    messages: list[dict[str, str]],
    temperature: float = 0.7,
    max_tokens: int | None = None,
) -> Generator[str, None, None]:
    """Stream chat completions, yielding content delta strings.

    Uses SSE (Server-Sent Events) via /v1/chat/completions with stream=true.
    Thinking mode is disabled — Qwen3's <think> blocks waste context budget.
    """
    body = {
        "model": config.OLLAMA_MODEL,
        "messages": messages,
        "stream": True,
        "temperature": temperature,
        "options": {
            "num_ctx": config.CTX_SIZE,
            "think": False,
        },
    }
    if max_tokens:
        body["max_tokens"] = max_tokens

    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{config.LLM_BASE_URL}/v1/chat/completions",
        data=data,
        headers=_headers(),
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=300) as resp:
        buf = b""
        while True:
            chunk = resp.read(4096)
            if not chunk:
                break
            buf += chunk
            # Process complete lines
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                if line == b"data: [DONE]":
                    return
                if line.startswith(b"data: "):
                    try:
                        obj = json.loads(line[6:])
                        delta = obj["choices"][0].get("delta", {})
                        content = delta.get("content")
                        if content:
                            yield content
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue


def complete_json(
    messages: list[dict[str, str]],
    temperature: float = 0.3,
    max_tokens: int = 512,
) -> str | None:
    """Non-streaming completion for structured extraction. Returns raw text."""
    body = {
        "model": config.OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "options": {
            "num_ctx": config.CTX_SIZE,
            "think": False,
        },
    }
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{config.LLM_BASE_URL}/v1/chat/completions",
        data=data,
        headers=_headers(),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            obj = json.loads(resp.read())
            return obj["choices"][0]["message"]["content"]
    except Exception:
        return None
