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

            # Prefer ChatCompletion for 'gpt' style models
            if "gpt" in model_name:
                resp = await _call_chat_completion(openai, model_name, req)
            else:
                resp = await _call_completion(openai, model_name, req)

            # Extract text and usage in a compact, compatible way
            text = _extract_text_from_response(resp)
            usage = _extract_usage_from_response(resp)

            return LLMResponse(model=model_name, content=text, usage_tokens=usage)

    # helper implementations kept local and simple
    async def _call_chat_completion(openai, model_name: str, req: LLMRequest):
        # Use create for older sync SDKs or an async shim; try both sync and async paths
        try:
            # modern openai Python lib exposes .ChatCompletion.create synchronously
            resp = openai.ChatCompletion.create(
                model=model_name,
                messages=[{"role": "user", "content": req.prompt}],
                max_tokens=req.max_tokens,
                temperature=req.temperature,
            )
            return resp
        except TypeError:
            # in case the SDK provides an async method (unlikely), await it
            return await openai.ChatCompletion.acreate(
                model=model_name,
                messages=[{"role": "user", "content": req.prompt}],
                max_tokens=req.max_tokens,
                temperature=req.temperature,
            )

    async def _call_completion(openai, model_name: str, req: LLMRequest):
        try:
            resp = openai.Completion.create(
                model=model_name,
                prompt=req.prompt,
                max_tokens=req.max_tokens,
                temperature=req.temperature,
            )
            return resp
        except TypeError:
            return await openai.Completion.acreate(
                model=model_name,
                prompt=req.prompt,
                max_tokens=req.max_tokens,
                temperature=req.temperature,
            )

    def _extract_text_from_response(resp: Any) -> str:
        # ChatCompletion shape: resp.choices[0].message.content or resp.choices[0]["message"]["content"]
        try:
            choice = resp.choices[0]
            # try attribute-style first
            msg = getattr(choice, "message", None)
            if msg is not None:
                return getattr(msg, "content", "")
            # fallback to mapping
            if isinstance(choice, dict):
                return choice.get("message", {}).get("content", "") or choice.get(
                    "text", ""
                )
            return getattr(choice, "text", "") or ""
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
            return 0
        except Exception:
            return 0

    return OpenAIProvider()
