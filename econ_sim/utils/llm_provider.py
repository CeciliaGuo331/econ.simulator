"""
LLM Provider 抽象与运行时绑定（OpenAI 兼容）。

该模块定义了用于与外部 LLM（当前以 OpenAI 兼容接口为主）交互的简单抽象：

- `LLMRequest` / `LLMResponse`：用于在模块内部标准化请求/响应的数据结构。
- `LLMProvider`：接口定义，具体 provider 应实现 `generate` 异步方法以返回 `LLMResponse`。
- `get_default_provider()`：按需在运行时导入 `openai` SDK 并返回一个 OpenAIProvider 实例，
    若 SDK 缺失或 `OPENAI_API_KEY` 未设置，会抛出明确的运行时错误以提示部署者配置环境。

设计原则：
- 避免在模块导入时立即依赖第三方 SDK；仅在需要 provider 时动态导入，从而降低导入侧副作用与测试难度。
- 若需要在测试中使用 mock，请在测试目录中实现并注入一个满足 `LLMProvider` 接口的模拟实现，
    或在测试 fixture 中替换 `get_default_provider` 的行为。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional
import os
import logging


@dataclass
class LLMRequest:
    model: str
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
    """Return an OpenAI-backed provider. Requires OPENAI_API_KEY and the `openai` package.

    Raises RuntimeError with a helpful message if configuration is missing.
    """
    try:
        import openai  # type: ignore
    except Exception as exc:  # pragma: no cover - diagnostic
        logging.exception("openai package import failed")
        raise RuntimeError(
            "The 'openai' package is required for the OpenAI LLM provider. Install it with 'pip install openai'."
        ) from exc

    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError(
            "OPENAI_API_KEY environment variable is not set; OpenAI provider cannot be used"
        )

    openai.api_key = key

    class OpenAIProvider(LLMProvider):
        async def generate(self, req: LLMRequest, *, user_id: str) -> LLMResponse:
            # Use ChatCompletion for gpt-like models, fallback to Completion for older models
            try:
                if req.model and "gpt" in req.model:
                    resp = openai.ChatCompletion.create(
                        model=req.model,
                        messages=[{"role": "user", "content": req.prompt}],
                        max_tokens=req.max_tokens,
                        temperature=req.temperature,
                    )
                    # compatibility: some SDKs return nested objects
                    choice = resp.choices[0]
                    text = (
                        getattr(choice.message, "content", None)
                        or choice["message"]["content"]
                    )
                    usage = (
                        int(resp.usage.get("total_tokens", 0))
                        if getattr(resp, "usage", None)
                        else 0
                    )
                else:
                    resp = openai.Completion.create(
                        model=req.model or "text-davinci-003",
                        prompt=req.prompt,
                        max_tokens=req.max_tokens,
                        temperature=req.temperature,
                    )
                    choice = resp.choices[0]
                    text = choice.text
                    usage = (
                        int(resp.usage.get("total_tokens", 0))
                        if getattr(resp, "usage", None)
                        else 0
                    )
                return LLMResponse(
                    model=req.model or "openai", content=text, usage_tokens=usage
                )
            except Exception:
                logging.exception("OpenAI provider generate failed")
                raise

    return OpenAIProvider()
