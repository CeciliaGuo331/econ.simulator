"""为用户策略脚本提供的轻量级 API。

此模块定义了脚本可用的便捷构建器与常用工具函数，供用户在
generate_decisions(context) 中调用以生成 TickDecisionOverrides。
API 设计强调最小权限与易用性：仅暴露必要的字段与数值工具。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

API_VERSION = 1

_HOUSEHOLD_FIELDS = {"consumption_budget", "savings_rate", "labor_supply"}
_FIRM_FIELDS = {"price", "planned_production", "wage_offer", "hiring_demand"}
_BANK_FIELDS = {"deposit_rate", "loan_rate", "loan_supply"}
_GOVERNMENT_FIELDS = {"tax_rate", "government_jobs", "transfer_budget"}
_GOVERNMENT_FIELDS = {"tax_rate", "government_jobs", "transfer_budget", "issuance_plan"}
_CENTRAL_BANK_FIELDS = {"policy_rate", "reserve_ratio"}


class OverridesBuilder:
    """帮助脚本构造 `TickDecisionOverrides` 结构。"""

    def __init__(self) -> None:
        self._households: Dict[int, Dict[str, Any]] = {}
        self._firm: Dict[str, Any] = {}
        self._bank: Dict[str, Any] = {}
        self._government: Dict[str, Any] = {}
        self._central_bank: Dict[str, Any] = {}
        self._bond_bids: list[Dict[str, Any]] = []

    def household(self, household_id: int, **fields: Any) -> "OverridesBuilder":
        _validate_fields("household", fields, _HOUSEHOLD_FIELDS)
        if fields:
            self._households[int(household_id)] = dict(fields)
        return self

    def firm(self, **fields: Any) -> "OverridesBuilder":
        _validate_fields("firm", fields, _FIRM_FIELDS)
        if fields:
            self._firm.update(fields)
        return self

    def bank(self, **fields: Any) -> "OverridesBuilder":
        _validate_fields("bank", fields, _BANK_FIELDS)
        if fields:
            self._bank.update(fields)
        return self

    def government(self, **fields: Any) -> "OverridesBuilder":
        _validate_fields("government", fields, _GOVERNMENT_FIELDS)
        if fields:
            # if issuance_plan provided, ensure it's a dict with allowed keys
            plan = fields.get("issuance_plan")
            if plan is not None:
                if not isinstance(plan, dict):
                    raise ValueError(
                        "issuance_plan must be a dict with keys 'volume' and optional 'min_price'"
                    )
            self._government.update(fields)
        return self

    def central_bank(self, **fields: Any) -> "OverridesBuilder":
        _validate_fields("central_bank", fields, _CENTRAL_BANK_FIELDS)
        if fields:
            self._central_bank.update(fields)
        return self

    def build(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        if self._households:
            result["households"] = self._households
        if self._firm:
            result["firm"] = self._firm
        if self._bank:
            result["bank"] = self._bank
        if self._government:
            result["government"] = self._government
        if self._central_bank:
            result["central_bank"] = self._central_bank
        if self._bond_bids:
            result["bond_bids"] = self._bond_bids
        return result

    def bond_bids(self, bids: list[Dict[str, Any]]) -> "OverridesBuilder":
        """Attach a list of bond bids to the decision overrides.

        Each bid should be a dict: {"buyer_kind": str, "buyer_id": str|int, "price": float, "quantity": float}
        This helper is intended for scripts that want to submit bids to the government's issuance process.
        """
        if bids:
            # minimal validation: ensure list of dict-like objects
            if not isinstance(bids, list):
                raise ValueError("bond_bids must be a list of bid dicts")
            self._bond_bids = list(bids)
        return self


def clamp(value: float, lower: float, upper: float) -> float:
    """在脚本中常用的数值裁剪函数。

    当 lower > upper 时抛出 ValueError；否则返回截断到 [lower, upper]
    区间内的值。
    """

    if lower > upper:
        raise ValueError("lower must not exceed upper")
    return max(lower, min(upper, value))


def fraction(numerator: float, denominator: float) -> float:
    """安全地计算比例，自动处理除零。

    若分母为零则返回 0.0，避免抛出 ZeroDivisionError。
    """

    if denominator == 0:
        return 0.0
    return numerator / denominator


def moving_average(series: Iterable[float], window: int) -> Optional[float]:
    """计算滑动平均值，若样本不足则返回 None。

    参数 window 必须为正整数；当序列长度小于 window 时返回 None。
    """

    data = list(series)
    if window <= 0 or len(data) < window:
        return None
    return sum(data[-window:]) / window


def _validate_fields(agent: str, provided: Dict[str, Any], allowed: set[str]) -> None:
    unknown = set(provided) - allowed
    if unknown:
        raise ValueError(
            f"{agent} override contains unsupported fields: {sorted(unknown)}"
        )
