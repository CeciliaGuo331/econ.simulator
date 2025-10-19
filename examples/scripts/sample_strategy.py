"""示例脚本：根据宏观指标调整家户消费与企业定价。"""

from typing import Any, Dict

from econ_sim.script_engine.user_api import OverridesBuilder, clamp
from econ_sim.utils.llm_session import LLMQuotaExceeded


def generate_decisions(context: Dict[str, Any]) -> Dict[str, Any]:
    """根据传入的世界状态生成 `TickDecisionOverrides` 兼容的字典。"""

    world = context["world_state"]
    macro = world["macro"]
    households_data = world["households"]

    builder = OverridesBuilder()

    # 依据 GDP 与通胀对家庭消费进行微调
    gdp_factor = clamp((macro["gdp"] / 5000.0) + 1.0, 0.8, 1.2)
    inflation_penalty = clamp(1.2 - macro["inflation"] * 2.0, 0.7, 1.2)

    for raw_id, data in households_data.items():
        hid = int(raw_id)
        wage_income = data.get("wage_income", 0.0)
        baseline = wage_income * 0.6
        adjusted_consumption = round(baseline * gdp_factor * inflation_penalty, 2)
        builder.household(
            hid,
            consumption_budget=adjusted_consumption,
            savings_rate=0.2,
        )

    # 企业侧根据库存与失业率调整价格与招聘需求
    firm = world["firm"]
    unemployment = macro["unemployment_rate"]
    inventory = firm["balance_sheet"]["inventory_goods"]
    target_inventory = 2.0 * max(1, len(households_data))

    price_adjustment = 1.0
    if inventory < target_inventory * 0.75:
        price_adjustment = 1.05
    elif inventory > target_inventory * 1.5:
        price_adjustment = 0.95

    hiring_delta = 2 if unemployment < 0.08 else -1

    builder.firm(
        price=round(firm["price"] * price_adjustment, 2),
        hiring_demand=max(0, firm.get("hiring_demand", 0) + hiring_delta),
    )

    builder.government(tax_rate=max(0.1, world["government"]["tax_rate"] - 0.01))

    # 示例：使用注入的 llm 辅助决策（如果可用）
    # 脚本运行时沙箱会在全局提供 `llm` 对象；若不可用则跳过。
    try:
        llm = globals().get("llm")
        if llm is not None:
            prompt = (
                "Given recent macro metrics, provide a one-sentence rationale for the firm's price adjustment: "
                f"gdp={macro.get('gdp')}, inflation={macro.get('inflation')}, unemployment={macro.get('unemployment_rate')}"
            )
            try:
                resp = llm.generate(prompt, max_tokens=40)
                # 将 LLM 建议附加到政府/firm 的 context 里，供审计/日志使用
                builder.firm(price=round(firm["price"] * price_adjustment, 2))
            except LLMQuotaExceeded:
                # 超出配额则安全回退，不影响主要决策
                pass
    except Exception:
        # 不要让辅助功能导致脚本失败
        pass

    return builder.build()
