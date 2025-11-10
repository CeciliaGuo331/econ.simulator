"""llm_provider
=================

简单的 OpenAI-compatible LLM provider 绑定，按环境变量初始化。

目标：尽量保持代码简单，只兼容本项目需要的 OpenAI 风格接口，并支持通过环境
变量配置三项：推理终点 (API base), API key, 与默认模型。

约定的环境变量（可以修改，如果你想要别的名字我可以改）：
- `LLM_API_ENDPOINT`：可选，OpenAI 兼容 API 的 base URL（例如自托管的推理终点）。
- `LLM_API_KEY`：必需，用于认证的 API key。
- `LLM_DEFAULT_MODEL`：可选，作为 provider 的默认 model 名称（例如 "gpt-3.5-turbo"）。

保留：模块仍定义 `LLMRequest` / `LLMResponse` / `LLMProvider` 抽象，以及 `get_default_provider()`。
实现尽量精简，token usage 的读取行为与之前兼容。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional
import httpx
import os
import logging


@dataclass
class LLMRequest:
    # Callers no longer select the model. This field is optional and ignored
    # by the provider implementation; the provider will use the system
    # configured default model instead.
    model: Optional[str]
    prompt: str
    max_tokens: int = 512
    temperature: float = 0.2


@dataclass
class LLMResponse:
    model: str
    content: str
    usage_tokens: int


class LLMProvider:
    """Provider interface for text generation."""

    async def generate(self, req: LLMRequest, *, user_id: str) -> LLMResponse:
        raise NotImplementedError


def get_default_provider() -> LLMProvider:
    # Read configuration from environment (three variables as requested):
    # - LLM_API_ENDPOINT (optional)
    # - LLM_API_KEY      (required)
    # - LLM_DEFAULT_MODEL(optional)
    endpoint = os.getenv("LLM_API_ENDPOINT") or os.getenv("OPENAI_API_BASE")
    key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    default_model = os.getenv("LLM_DEFAULT_MODEL") or os.getenv("OPENAI_MODEL")

    if not key:
        raise RuntimeError(
            "LLM API key is not set. Set LLM_API_KEY (or OPENAI_API_KEY) in environment."
        )

    try:
        import openai  # type: ignore
    except Exception as exc:  # pragma: no cover - diagnostic
        logging.exception("openai package import failed")
        raise RuntimeError(
            "The 'openai' package is required for the OpenAI-compatible provider. Install it with 'pip install openai'."
        ) from exc

    # Apply configuration to openai client
    openai.api_key = key
    if endpoint:
        # allow custom inference endpoints (OpenAI-compatible)
        openai.api_base = endpoint

    class OpenAIProvider(LLMProvider):
        async def generate(self, req: LLMRequest, *, user_id: str) -> LLMResponse:
            # Ignore any model provided by the caller; enforce system-provided model
            model_name = default_model or "gpt-3.5-turbo"

            # Use Chat Completions REST endpoint (matches the provided example)
            resp = await _call_chat_completions_api(model_name, req, key, endpoint)

            # Extract text and usage in a compact, compatible way
            text = _extract_text_from_response(resp)
            usage = _extract_usage_from_response(resp)

            return LLMResponse(model=model_name, content=text, usage_tokens=usage)

    # helper implementations kept local and simple
    async def _call_chat_completions_api(
        model_name: str, req: LLMRequest, api_key: str, api_base: Optional[str]
    ):
        """Call the Chat Completions REST endpoint using async httpx.

        This mirrors the official example: POST {api_base}/v1/chat/completions
        with Authorization Bearer <key> and a JSON body containing `model` and
        `messages`. We use a non-streaming call for simplicity.
        """
        if not api_base:
            raise RuntimeError("No API base configured for chat completions endpoint")

        url = api_base.rstrip("/") + "/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        body = {
            "model": model_name,
            "messages": [{"role": "user", "content": req.prompt}],
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
        }

        async with httpx.AsyncClient(timeout=30.0, verify=True) as client:
            resp = await client.post(url, headers=headers, json=body)
            text = resp.text
            try:
                data = resp.json()
            except Exception:
                raise RuntimeError(
                    f"Non-JSON response from LLM endpoint: {resp.status_code} {text[:200]}"
                )

            if resp.status_code >= 400:
                # Bubble up a compact error without leaking the key
                err_msg = data.get("error") if isinstance(data, dict) else text
                raise RuntimeError(f"LLM endpoint error {resp.status_code}: {err_msg}")

            return data

    # remove legacy completion/chat code paths; use responses API only

    def _extract_text_from_response(resp: Any) -> str:
        """Extract textual content from a Responses API response.

        This focuses on the new Responses API shapes but keeps a minimal
        fallback to older 'choices' style if present for robustness.
        """
        try:
            # common new-style attribute
            out_text = getattr(resp, "output_text", None)
            if out_text:
                return out_text

            # mapping-style new responses API
            if isinstance(resp, dict):
                if resp.get("output_text"):
                    return resp.get("output_text")
                outputs = resp.get("output") or resp.get("outputs")
                if outputs and isinstance(outputs, list) and len(outputs) > 0:
                    first = outputs[0]
                    if isinstance(first, dict):
                        content = first.get("content")
                        if isinstance(content, list) and len(content) > 0:
                            c0 = content[0]
                            if isinstance(c0, dict):
                                return c0.get("text") or c0.get("content") or ""
                            if isinstance(c0, str):
                                return c0
                        if first.get("text"):
                            return first.get("text")

            # attribute-style outputs
            outputs = getattr(resp, "output", None) or getattr(resp, "outputs", None)
            if outputs and isinstance(outputs, list) and len(outputs) > 0:
                first = outputs[0]
                content = None
                if isinstance(first, dict):
                    content = first.get("content")
                else:
                    content = getattr(first, "content", None)
                if isinstance(content, list) and len(content) > 0:
                    item = content[0]
                    if isinstance(item, dict):
                        return item.get("text") or ""
                    if isinstance(item, str):
                        return item

            # minimal fallback to legacy choices API
            choice = getattr(resp, "choices", None)
            if choice:
                c = choice[0]
                msg = getattr(c, "message", None)
                if msg:
                    return getattr(msg, "content", "")
                if isinstance(c, dict):
                    return c.get("message", {}).get("content", "") or c.get("text", "")
                return getattr(c, "text", "") or ""
        except Exception:
            logging.exception("Failed to extract text from LLM response")
        return ""

    def _extract_usage_from_response(resp: Any) -> int:
        try:
            usage = getattr(resp, "usage", None)
            if usage and isinstance(usage, dict):
                return int(usage.get("total_tokens", 0) or 0)
            if usage and hasattr(usage, "total_tokens"):
                return int(getattr(usage, "total_tokens", 0) or 0)
            # also check mapping style
            if isinstance(resp, dict):
                u = resp.get("usage") or {}
                if isinstance(u, dict) and u.get("total_tokens"):
                    return int(u.get("total_tokens") or 0)
            return 0
        except Exception:
            return 0

    provider = OpenAIProvider()
    # Expose the system model used by this provider instance so callers
    # (HTTP endpoints, adapters) can report which model is actually used.
    provider.system_model = default_model or "gpt-3.5-turbo"
    return provider
