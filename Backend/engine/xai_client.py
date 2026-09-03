"""
xAI (Grok) — the second language-model provider.

Sits between Gemini and the rule-based fallback: when Gemini is unavailable,
rate-limited or out of quota, the same prompts are sent here before the system
gives up on AI-written output altogether.

The xAI API is OpenAI-compatible, so this talks to it over plain HTTP with
httpx rather than pulling in another SDK.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator

import httpx

import config

log = logging.getLogger("nidhi.xai")

CHAT_PATH = "/chat/completions"


def available() -> bool:
    return bool(config.XAI_API_KEY)


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {config.XAI_API_KEY}",
        "Content-Type": "application/json",
    }


def _body(prompt: str, *, json_mode: bool, stream: bool,
          temperature: float) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": config.XAI_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "stream": stream,
    }
    if json_mode:
        # xAI follows the OpenAI contract here. The prompts already spell out
        # the shape they expect, and _extract_json repairs anything untidy.
        body["response_format"] = {"type": "json_object"}
    return body


def _describe_failure(response: httpx.Response) -> str:
    try:
        payload = response.json()
        detail = payload.get("error") or payload.get("message") or response.text
        code = payload.get("code", "")
    except Exception:
        detail, code = response.text, ""
    return f"HTTP {response.status_code} {code}: {str(detail)[:200]}".strip()


async def generate(prompt: str, *, json_schema: dict | None = None,
                   temperature: float = 0.4) -> str | None:
    """One completion. Returns None on any failure; never raises."""
    if not available():
        return None

    text_prompt = prompt
    if json_schema is not None:
        # Gemini takes a schema object; xAI's json_object mode does not, so the
        # shape is given to the model in the prompt instead.
        text_prompt = (f"{prompt}\n\nRespond with a single JSON object matching "
                       f"this schema:\n{json.dumps(json_schema)}")

    try:
        async with httpx.AsyncClient(base_url=config.XAI_BASE_URL,
                                     timeout=config.XAI_TIMEOUT) as client:
            response = await client.post(
                CHAT_PATH, headers=_headers(),
                json=_body(text_prompt, json_mode=json_schema is not None,
                           stream=False, temperature=temperature))
        if response.status_code != 200:
            log.warning("xAI call failed: %s", _describe_failure(response))
            return None
        choices = response.json().get("choices") or []
        if not choices:
            log.warning("xAI returned no choices")
            return None
        return (choices[0].get("message") or {}).get("content") or None
    except Exception as exc:
        log.warning("xAI call failed: %s: %s", type(exc).__name__, str(exc)[:200])
        return None


async def generate_stream(prompt: str, *, temperature: float = 0.5
                          ) -> AsyncIterator[str]:
    """Stream text chunks. Yields nothing if the provider is unavailable."""
    if not available():
        return
    try:
        async with httpx.AsyncClient(base_url=config.XAI_BASE_URL,
                                     timeout=config.XAI_TIMEOUT) as client:
            async with client.stream(
                "POST", CHAT_PATH, headers=_headers(),
                json=_body(prompt, json_mode=False, stream=True,
                           temperature=temperature),
            ) as response:
                if response.status_code != 200:
                    await response.aread()
                    log.warning("xAI stream failed: %s", _describe_failure(response))
                    return
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = line[6:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        delta = (json.loads(payload)["choices"][0]
                                 .get("delta") or {})
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
                    if (chunk := delta.get("content")):
                        yield chunk
    except Exception as exc:
        log.warning("xAI stream failed: %s: %s", type(exc).__name__, str(exc)[:200])


async def health_check() -> tuple[bool, str]:
    """Cheap liveness probe for GET /health."""
    if not available():
        return False, "XAI_API_KEY not configured"
    try:
        async with httpx.AsyncClient(base_url=config.XAI_BASE_URL,
                                     timeout=min(config.XAI_TIMEOUT, 15)) as client:
            response = await client.post(
                CHAT_PATH, headers=_headers(),
                json={"model": config.XAI_MODEL,
                      "messages": [{"role": "user", "content": "ok"}],
                      "max_tokens": 4})
        if response.status_code == 200:
            return True, "ok"
        return False, _describe_failure(response)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"[:160]
