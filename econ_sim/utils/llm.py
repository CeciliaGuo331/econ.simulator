"""
LLM 适配层：为上层代码提供简单的一致接口（complete）以调用底层 provider 的生成能力。

说明：
- 该模块把底层的 `LLMProvider.generate(LLMRequest, user_id=...)` 封装为 `complete(prompt, model, max_tokens)`，
    返回字符串结果，便于 API 层或脚本直接使用而不暴露底层数据结构。
- 运行时会调用 `econ_sim.utils.llm_provider.get_default_provider()` 来获得实际 provider 实例，
    因此部署时需确保 `openai` SDK 与 `OPENAI_API_KEY` 已正确配置（默认 provider 为 openai）。
"""

from __future__ import annotations

from typing import Optional

from .llm_provider import LLMRequest, get_default_provider


class _Adapter:
    """Adapter exposing async complete(prompt, model, max_tokens) -> str

    It delegates to the provider.generate(LLMRequest, user_id=...) and
    returns the response content as string.
    """

    def __init__(self, provider):
        self._provider = provider

    async def complete(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        req = LLMRequest(
            model=model or "gpt-3.5-turbo",
            prompt=prompt,
            max_tokens=int(max_tokens or 256),
        )
        resp = await self._provider.generate(req, user_id="api")
        return getattr(resp, "content", str(resp))


def resolve_llm_provider() -> _Adapter:
    provider = get_default_provider()
    return _Adapter(provider)
