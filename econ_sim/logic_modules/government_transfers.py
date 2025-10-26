"""政府转移支付实现（最小可运行版）。

包含：
- means_tested_transfer：对净资产低于阈值的家庭发放固定金额补助
- unemployment_benefit：对当前失业的家庭发放固定金额失业补助

实现原则：
- 优先使用 `decisions.government.transfer_budget` 作为当期资金，否则使用 government.balance_sheet.cash
- 支付通过生成 StateUpdateCommand.assign（写入更新后的 balance_sheet）实现
- 记录 LedgerEntry 与 TickLogEntry 以便测试断言
- 当资金不足且配置允许时，允许通过 "发债"（在本最小实现中以把政府现金变为负数并增加一个 bond_issuance ledger 记录表示）来补足
"""

from __future__ import annotations

from typing import List, Tuple, Optional, Dict, Any
import json

from ..data_access.models import (
    WorldState,
    GovernmentDecision,
    StateUpdateCommand,
    TickLogEntry,
    LedgerEntry,
    AgentKind,
    HouseholdState,
)
from ..utils.settings import get_world_config


def means_tested_transfer(
    world_state: WorldState,
    gov_decision: GovernmentDecision,
    bids: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[List[StateUpdateCommand], List[LedgerEntry], TickLogEntry]:
    # optional bids param will be passed by caller via decisions.bond_bids when available
    return _means_tested_transfer(world_state, gov_decision, bids=bids)


def _means_tested_transfer(
    world_state: WorldState,
    gov_decision: GovernmentDecision,
    bids: Optional[List[Dict[str, Any]]],
) -> Tuple[List[StateUpdateCommand], List[LedgerEntry], TickLogEntry]:
    cfg = get_world_config()
    gov = world_state.government

    # parameters
    threshold = cfg.policies.transfer_threshold
    per_person = cfg.policies.means_test_amount
    funding_policy = cfg.policies.transfer_funding_policy
    allow_partial = cfg.policies.allow_partial_payment

    # available budget
    budget = (
        gov_decision.transfer_budget
        if getattr(gov_decision, "transfer_budget", None) is not None
        else gov.balance_sheet.cash
    )
    budget = float(budget or 0.0)

    # find beneficiaries
    beneficiaries = [
        hid
        for hid, h in world_state.households.items()
        if float(h.balance_sheet.cash) < float(threshold)
    ]

    total_need = per_person * len(beneficiaries)

    updates: List[StateUpdateCommand] = []
    ledger: List[LedgerEntry] = []

    if not beneficiaries:
        log = TickLogEntry(
            tick=world_state.tick,
            day=world_state.day,
            message="means_tested_transfer_skipped",
            context={
                "beneficiary_count": 0,
                "total_paid": 0.0,
                "funding_method": "none",
            },
        )
        return updates, ledger, log

    if budget >= total_need:
        # full payment
        paid_per = per_person
        total_paid = total_need
        funding_method = "taxes"
    else:
        shortfall = total_need - budget
        if funding_policy == "allow_debt":
            # allow government to go negative (record bond issuance)
            paid_per = per_person
            total_paid = total_need
            funding_method = "debt"
        elif allow_partial and budget > 0:
            paid_per = budget / len(beneficiaries)
            total_paid = budget
            funding_method = "partial"
        else:
            # cannot pay
            paid_per = 0.0
            total_paid = 0.0
            funding_method = "insufficient"

    # apply payments
    for hid in beneficiaries:
        if paid_per <= 0:
            break
        h: HouseholdState = world_state.households[hid]
        new_cash = float(h.balance_sheet.cash) + paid_per
        # write back full balance_sheet as in other modules
        new_bs = h.balance_sheet.model_dump()
        new_bs["cash"] = new_cash
        updates.append(
            StateUpdateCommand.assign(
                AgentKind.HOUSEHOLD,
                agent_id=hid,
                balance_sheet=new_bs,
            )
        )
        ledger.append(
            LedgerEntry(
                tick=world_state.tick,
                day=world_state.day,
                account_kind=AgentKind.GOVERNMENT,
                entity_id=gov.id,
                entry_type="transfer_payment",
                amount=-paid_per,
                balance_after=None,
            )
        )
        ledger.append(
            LedgerEntry(
                tick=world_state.tick,
                day=world_state.day,
                account_kind=AgentKind.HOUSEHOLD,
                entity_id=str(hid),
                entry_type="transfer_receipt",
                amount=paid_per,
                balance_after=None,
            )
        )

    # update government cash
    if total_paid > 0:
        new_gov_cash = float(gov.balance_sheet.cash) - total_paid
        new_gov_bs = gov.balance_sheet.model_dump()
        new_gov_bs["cash"] = new_gov_cash
        updates.append(
            StateUpdateCommand.assign(
                AgentKind.GOVERNMENT,
                agent_id=gov.id,
                balance_sheet=new_gov_bs,
            )
        )

    # record bond issuance or call marketized issuance if used debt
    if funding_method == "debt" and total_paid > 0 and budget < total_need:
        shortfall = total_need - budget
        # try marketized issuance: call government_financial.issue_bonds with provided bids if any
        try:
            from . import government_financial

            # prepare bids: prefer passed bids, else default to bank underwriting at par
            use_bids = (
                bids
                if bids
                else [
                    {
                        "buyer_kind": "bank",
                        "buyer_id": world_state.bank.id,
                        "price": 1.0,
                        "quantity": shortfall,
                    }
                ]
            )
            res = government_financial.issue_bonds(
                world_state,
                face_value=1.0,
                coupon_rate=cfg.policies.default_bond_coupon_rate,
                maturity_tick=world_state.tick + cfg.policies.default_bond_maturity,
                volume=shortfall,
                bids=use_bids,
                tick=world_state.tick,
                day=world_state.day,
                issuance_plan=getattr(gov_decision, "issuance_plan", None),
            )
            # integrate returned ledgers and updates for bookkeeping
            for up in res.get("updates", []):
                updates.append(up)
            for le in res.get("ledgers", []):
                ledger.append(le)
            # if auction produced a market log, include trade info into our log context
            auction_log = res.get("auction_log")
            if auction_log is not None:
                try:
                    # attach serialized trades into our ledger/log context via a marker entry
                    ledger.append(
                        LedgerEntry(
                            tick=world_state.tick,
                            day=world_state.day,
                            account_kind=AgentKind.GOVERNMENT,
                            entity_id=gov.id,
                            entry_type="bond_auction_log",
                            amount=0.0,
                            balance_after=None,
                            reference=(
                                res.get("bond", {}).id if res.get("bond") else None
                            ),
                        )
                    )
                except Exception:
                    pass
                # also capture trades JSON to include in TickLogEntry context later
                try:
                    auction_trades_json = auction_log.context.get("trades")
                except Exception:
                    auction_trades_json = None
            else:
                auction_trades_json = None
            # keep a compatibility bond_issuance ledger marker as well
            ledger.append(
                LedgerEntry(
                    tick=world_state.tick,
                    day=world_state.day,
                    account_kind=AgentKind.GOVERNMENT,
                    entity_id=gov.id,
                    entry_type="bond_issuance",
                    amount=shortfall,
                    balance_after=None,
                )
            )
        except Exception:
            # fallback: legacy behavior (negative cash + bond_issuance ledger)
            ledger.append(
                LedgerEntry(
                    tick=world_state.tick,
                    day=world_state.day,
                    account_kind=AgentKind.GOVERNMENT,
                    entity_id=gov.id,
                    entry_type="bond_issuance",
                    amount=shortfall,
                    balance_after=None,
                )
            )

    context = {
        "beneficiary_count": len(beneficiaries),
        "total_paid": float(total_paid),
        "funding_method": funding_method,
        "beneficiaries": json.dumps(beneficiaries),
    }
    if auction_trades_json is not None:
        context["bond_auction_trades"] = auction_trades_json
    log = TickLogEntry(
        tick=world_state.tick,
        day=world_state.day,
        message="means_tested_transfer_executed",
        context=context,
    )

    return updates, ledger, log


def unemployment_benefit(
    world_state: WorldState,
    gov_decision: GovernmentDecision,
    bids: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[List[StateUpdateCommand], List[LedgerEntry], TickLogEntry]:
    return _unemployment_benefit(world_state, gov_decision, bids=bids)


def _unemployment_benefit(
    world_state: WorldState,
    gov_decision: GovernmentDecision,
    bids: Optional[List[Dict[str, Any]]],
) -> Tuple[List[StateUpdateCommand], List[LedgerEntry], TickLogEntry]:
    cfg = get_world_config()
    gov = world_state.government

    amount = cfg.policies.unemployment_benefit
    funding_policy = cfg.policies.transfer_funding_policy
    allow_partial = cfg.policies.allow_partial_payment

    budget = (
        gov_decision.transfer_budget
        if getattr(gov_decision, "transfer_budget", None) is not None
        else gov.balance_sheet.cash
    )
    budget = float(budget or 0.0)

    unemployed = [
        hid
        for hid, h in world_state.households.items()
        if h.employment_status.name == "UNEMPLOYED"
    ]
    total_need = amount * len(unemployed)

    updates: List[StateUpdateCommand] = []
    ledger: List[LedgerEntry] = []

    if not unemployed:
        log = TickLogEntry(
            tick=world_state.tick,
            day=world_state.day,
            message="unemployment_benefit_skipped",
            context={
                "beneficiary_count": 0,
                "total_paid": 0.0,
                "funding_method": "none",
            },
        )
        return updates, ledger, log

    if budget >= total_need:
        paid_per = amount
        total_paid = total_need
        funding_method = "taxes"
    else:
        shortfall = total_need - budget
        if funding_policy == "allow_debt":
            paid_per = amount
            total_paid = total_need
            funding_method = "debt"
        elif allow_partial and budget > 0:
            paid_per = budget / len(unemployed)
            total_paid = budget
            funding_method = "partial"
        else:
            paid_per = 0.0
            total_paid = 0.0
            funding_method = "insufficient"

    for hid in unemployed:
        if paid_per <= 0:
            break
        h = world_state.households[hid]
        new_cash = float(h.balance_sheet.cash) + paid_per
        new_bs = h.balance_sheet.model_dump()
        new_bs["cash"] = new_cash
        updates.append(
            StateUpdateCommand.assign(
                AgentKind.HOUSEHOLD,
                agent_id=hid,
                balance_sheet=new_bs,
            )
        )
        ledger.append(
            LedgerEntry(
                tick=world_state.tick,
                day=world_state.day,
                account_kind=AgentKind.GOVERNMENT,
                entity_id=gov.id,
                entry_type="unemployment_payment",
                amount=-paid_per,
                balance_after=None,
            )
        )
        ledger.append(
            LedgerEntry(
                tick=world_state.tick,
                day=world_state.day,
                account_kind=AgentKind.HOUSEHOLD,
                entity_id=str(hid),
                entry_type="unemployment_receipt",
                amount=paid_per,
                balance_after=None,
            )
        )

    if total_paid > 0:
        new_gov_cash = float(gov.balance_sheet.cash) - total_paid
        new_gov_bs = gov.balance_sheet.model_dump()
        new_gov_bs["cash"] = new_gov_cash
        updates.append(
            StateUpdateCommand.assign(
                AgentKind.GOVERNMENT,
                agent_id=gov.id,
                balance_sheet=new_gov_bs,
            )
        )

    if funding_method == "debt" and total_paid > 0 and budget < total_need:
        shortfall = total_need - budget
        try:
            from . import government_financial

            use_bids = (
                bids
                if bids
                else [
                    {
                        "buyer_kind": "bank",
                        "buyer_id": world_state.bank.id,
                        "price": 1.0,
                        "quantity": shortfall,
                    }
                ]
            )
            res = government_financial.issue_bonds(
                world_state,
                face_value=1.0,
                coupon_rate=cfg.policies.default_bond_coupon_rate,
                maturity_tick=world_state.tick + cfg.policies.default_bond_maturity,
                volume=shortfall,
                bids=use_bids,
                tick=world_state.tick,
                day=world_state.day,
                issuance_plan=getattr(gov_decision, "issuance_plan", None),
            )
            for up in res.get("updates", []):
                updates.append(up)
            for le in res.get("ledgers", []):
                ledger.append(le)
            # capture auction log marker if present
            auction_log = res.get("auction_log")
            if auction_log is not None:
                try:
                    ledger.append(
                        LedgerEntry(
                            tick=world_state.tick,
                            day=world_state.day,
                            account_kind=AgentKind.GOVERNMENT,
                            entity_id=gov.id,
                            entry_type="bond_auction_log",
                            amount=0.0,
                            balance_after=None,
                            reference=(
                                res.get("bond", {}).id if res.get("bond") else None
                            ),
                        )
                    )
                except Exception:
                    pass
                try:
                    auction_trades_json = auction_log.context.get("trades")
                except Exception:
                    auction_trades_json = None
            else:
                auction_trades_json = None

            ledger.append(
                LedgerEntry(
                    tick=world_state.tick,
                    day=world_state.day,
                    account_kind=AgentKind.GOVERNMENT,
                    entity_id=gov.id,
                    entry_type="bond_issuance",
                    amount=shortfall,
                    balance_after=None,
                )
            )
        except Exception:
            ledger.append(
                LedgerEntry(
                    tick=world_state.tick,
                    day=world_state.day,
                    account_kind=AgentKind.GOVERNMENT,
                    entity_id=gov.id,
                    entry_type="bond_issuance",
                    amount=shortfall,
                    balance_after=None,
                )
            )

    context = {
        "beneficiary_count": len(unemployed),
        "total_paid": float(total_paid),
        "funding_method": funding_method,
        "beneficiaries": json.dumps(unemployed),
    }
    try:
        if auction_trades_json is not None:
            context["bond_auction_trades"] = auction_trades_json
    except Exception:
        pass
    log = TickLogEntry(
        tick=world_state.tick,
        day=world_state.day,
        message="unemployment_benefit_executed",
        context=context,
    )

    return updates, ledger, log
