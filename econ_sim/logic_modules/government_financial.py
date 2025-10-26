"""政府的正式债券发行流程（最小化实现）。

功能：
- issue_bonds(world_state, face_value, coupon_rate, maturity_tick, volume, bids, tick, day)
  创建 BondInstrument，调用 bond_market.clear_bond_auction 完成承销，返回 updates/ledgers/trades/market_yield
"""

from __future__ import annotations

from uuid import uuid4
from typing import List, Dict, Any

from econ_sim.data_access import models
from econ_sim.data_access.models import BondInstrument, AgentKind
from . import bond_market
from econ_sim.data_access.models import StateUpdateCommand
from econ_sim.utils.settings import get_world_config


def issue_bonds(
    world_state: models.WorldState,
    face_value: float,
    coupon_rate: float,
    maturity_tick: int,
    volume: float,
    bids: List[Dict[str, Any]],
    tick: int,
    day: int,
    issuance_plan: dict | None = None,
    coupon_frequency_ticks: int = 0,
):
    """发行债券并进行市场化承销。

    bids: list of {buyer_kind, buyer_id, price, quantity}
    """
    bond_id = f"bond_{tick}_{uuid4().hex[:8]}"

    bond = BondInstrument(
        id=bond_id,
        issuer=world_state.government.id,
        face_value=face_value,
        coupon_rate=coupon_rate,
        coupon_frequency_ticks=coupon_frequency_ticks,
        next_coupon_tick=(
            (tick + coupon_frequency_ticks)
            if coupon_frequency_ticks and coupon_frequency_ticks > 0
            else maturity_tick
        ),
        maturity_tick=maturity_tick,
        outstanding=volume,
        holders={},
    )

    # run auction / underwriting
    # if issuer provided an issuance_plan, it may override volume and set a min_price
    min_price = None
    try:
        if issuance_plan is not None and isinstance(issuance_plan, dict):
            if issuance_plan.get("volume") is not None:
                try:
                    bond.outstanding = float(issuance_plan.get("volume"))
                except Exception:
                    pass
            if issuance_plan.get("min_price") is not None:
                try:
                    min_price = float(issuance_plan.get("min_price"))
                except Exception:
                    min_price = None
    except Exception:
        min_price = None

    result = bond_market.clear_bond_auction(
        world_state, bond, bids, tick=tick, day=day, min_price=min_price
    )

    # update public market data bond_yield if available
    if result.get("market_yield") is not None:
        try:
            market_yield = result.get("market_yield")
            # write market yield back to macro state so strategies can observe it
            macro_update = StateUpdateCommand.assign(
                AgentKind.MACRO, agent_id=None, bond_yield=market_yield
            )
            # append macro update to returned updates
            result_updates = result.get("updates", [])
            result_updates.append(macro_update)
            result["updates"] = result_updates
        except Exception:
            pass

    # forward auction log (if present) under a consistent key so callers may persist it
    if result.get("log") is not None:
        result["auction_log"] = result.get("log")

    return {"bond": bond, **result}


def process_coupon_payments(world_state: models.WorldState, tick: int, day: int):
    """处理当前 tick 的周期性 coupon 支付。

    支持按 bond.coupon_frequency_ticks 调度。若政府现金不足，则按比例（pro-rata）部分支付。
    返回值：updates, ledgers, log
    """
    government = world_state.government
    updates = []
    ledgers = []
    matured = []

    # ticks per year for annualization (use world config if available)
    try:
        cfg = get_world_config()
        ticks_per_year = int(cfg.simulation.ticks_per_day * 365)
    except Exception:
        ticks_per_year = 365

    for bond_id, bond in list(government.debt_instruments.items()):
        # skip zero-frequency (only pay at maturity)
        if not bond.coupon_frequency_ticks or bond.coupon_frequency_ticks <= 0:
            continue

        if bond.next_coupon_tick is None or bond.next_coupon_tick > tick:
            continue

        # coupon_rate is interpreted as per-tick interest rate (simplified model)
        # therefore coupon per unit for the period spanning `coupon_frequency_ticks` ticks is:
        coupon_per_unit = (
            bond.face_value * bond.coupon_rate * bond.coupon_frequency_ticks
        )

        # compute total coupon due, but only include holdings that have been held at least one day
        total_due = 0.0
        holder_amounts = {}
        try:
            cfg = get_world_config()
            min_hold_ticks = int(cfg.simulation.ticks_per_day)
        except Exception:
            min_hold_ticks = 1

        # helper to compute eligible quantity for a buyer
        def eligible_qty_for(buyer_id_str: str) -> float:
            # if purchase_records exist, sum quantities with purchase_tick <= tick - min_hold_ticks
            if getattr(bond, "purchase_records", None):
                qty = 0.0
                for rec in bond.purchase_records:
                    try:
                        if str(rec.get("buyer_id")) == str(buyer_id_str) and int(
                            rec.get("tick", 0)
                        ) <= (tick - min_hold_ticks):
                            qty += float(rec.get("quantity", 0.0))
                    except Exception:
                        continue
                return qty
            # fallback: use aggregate holdings
            owner_qty = 0.0
            if buyer_id_str == (
                getattr(world_state.bank, "id", None)
                if getattr(world_state, "bank", None)
                else None
            ):
                owner_qty = (
                    float(
                        getattr(world_state.bank, "bond_holdings", {}).get(bond_id, 0.0)
                    )
                    if getattr(world_state, "bank", None)
                    else 0.0
                )
            else:
                try:
                    hid = int(buyer_id_str)
                    owner_qty = float(
                        world_state.households[hid].bond_holdings.get(bond_id, 0.0)
                    )
                except Exception:
                    owner_qty = 0.0
            return owner_qty

        # bank
        bank = getattr(world_state, "bank", None)
        if bank is not None:
            bqty = eligible_qty_for(bank.id)
            if bqty and bqty > 0:
                amt = bqty * coupon_per_unit
                holder_amounts[(AgentKind.BANK, bank.id)] = amt
                total_due += amt

        # households
        for hid, hh in getattr(world_state, "households", {}).items():
            hid_str = str(hid)
            hqty = eligible_qty_for(hid_str)
            if hqty and hqty > 0:
                amt = hqty * coupon_per_unit
                holder_amounts[(AgentKind.HOUSEHOLD, hid_str)] = amt
                total_due += amt

        if total_due <= 0:
            # advance next_coupon_tick to avoid infinite loop
            bond.next_coupon_tick = bond.next_coupon_tick + bond.coupon_frequency_ticks
            continue

        gov_cash = government.balance_sheet.cash
        if gov_cash >= total_due:
            # full payment
            for (kind, eid), amt in holder_amounts.items():
                if (
                    kind == AgentKind.BANK
                    and getattr(world_state, "bank", None) is not None
                    and str(eid) == str(world_state.bank.id)
                ):
                    bank.balance_sheet.cash += amt
                    ledgers.append(
                        models.LedgerEntry(
                            tick=tick,
                            day=day,
                            account_kind=AgentKind.BANK,
                            entity_id=bank.id,
                            entry_type="coupon_receipt",
                            amount=amt,
                            balance_after=bank.balance_sheet.cash,
                            reference=bond_id,
                        )
                    )
                    updates.append(
                        StateUpdateCommand.assign(
                            scope=AgentKind.BANK,
                            agent_id=bank.id,
                            balance_sheet=bank.balance_sheet.model_dump(),
                            bond_holdings=bank.bond_holdings,
                        )
                    )
                else:
                    # household
                    hid = int(eid)
                    hh = world_state.households[hid]
                    hh.balance_sheet.cash += amt
                    ledgers.append(
                        models.LedgerEntry(
                            tick=tick,
                            day=day,
                            account_kind=AgentKind.HOUSEHOLD,
                            entity_id=str(hid),
                            entry_type="coupon_receipt",
                            amount=amt,
                            balance_after=hh.balance_sheet.cash,
                            reference=bond_id,
                        )
                    )
                    updates.append(
                        StateUpdateCommand.assign(
                            scope=AgentKind.HOUSEHOLD,
                            agent_id=hid,
                            balance_sheet=hh.balance_sheet.model_dump(),
                            bond_holdings=hh.bond_holdings,
                        )
                    )

            # deduct from government
            government.balance_sheet.cash -= total_due
            ledgers.append(
                models.LedgerEntry(
                    tick=tick,
                    day=day,
                    account_kind=AgentKind.GOVERNMENT,
                    entity_id=government.id,
                    entry_type="coupon_payment",
                    amount=-total_due,
                    balance_after=government.balance_sheet.cash,
                    reference=bond_id,
                )
            )
            updates.append(
                StateUpdateCommand.assign(
                    scope=AgentKind.GOVERNMENT,
                    agent_id=government.id,
                    balance_sheet=government.balance_sheet.model_dump(),
                    debt_outstanding=government.debt_outstanding,
                )
            )
        else:
            # partial pro-rata payment
            fraction = gov_cash / total_due if total_due > 0 else 0.0
            paid = 0.0
            for (kind, eid), amt in holder_amounts.items():
                pay = amt * fraction
                paid += pay
                if (
                    kind == AgentKind.BANK
                    and getattr(world_state, "bank", None) is not None
                    and str(eid) == str(world_state.bank.id)
                ):
                    bank.balance_sheet.cash += pay
                    ledgers.append(
                        models.LedgerEntry(
                            tick=tick,
                            day=day,
                            account_kind=AgentKind.BANK,
                            entity_id=bank.id,
                            entry_type="coupon_receipt_partial",
                            amount=pay,
                            balance_after=bank.balance_sheet.cash,
                            reference=bond_id,
                        )
                    )
                    updates.append(
                        StateUpdateCommand.assign(
                            scope=AgentKind.BANK,
                            agent_id=bank.id,
                            balance_sheet=bank.balance_sheet.model_dump(),
                            bond_holdings=bank.bond_holdings,
                        )
                    )
                else:
                    hid = int(eid)
                    hh = world_state.households[hid]
                    hh.balance_sheet.cash += pay
                    ledgers.append(
                        models.LedgerEntry(
                            tick=tick,
                            day=day,
                            account_kind=AgentKind.HOUSEHOLD,
                            entity_id=str(hid),
                            entry_type="coupon_receipt_partial",
                            amount=pay,
                            balance_after=hh.balance_sheet.cash,
                            reference=bond_id,
                        )
                    )
                    updates.append(
                        StateUpdateCommand.assign(
                            scope=AgentKind.HOUSEHOLD,
                            agent_id=hid,
                            balance_sheet=hh.balance_sheet.model_dump(),
                            bond_holdings=hh.bond_holdings,
                        )
                    )

            # deduct paid amount from government (set to zero)
            government.balance_sheet.cash -= paid
            ledgers.append(
                models.LedgerEntry(
                    tick=tick,
                    day=day,
                    account_kind=AgentKind.GOVERNMENT,
                    entity_id=government.id,
                    entry_type="coupon_payment_partial",
                    amount=-paid,
                    balance_after=government.balance_sheet.cash,
                    reference=bond_id,
                )
            )
            updates.append(
                StateUpdateCommand.assign(
                    scope=AgentKind.GOVERNMENT,
                    agent_id=government.id,
                    balance_sheet=government.balance_sheet.model_dump(),
                    debt_outstanding=government.debt_outstanding,
                )
            )

        # advance next coupon tick
        bond.next_coupon_tick = bond.next_coupon_tick + bond.coupon_frequency_ticks

    log = models.TickLogEntry(
        tick=tick,
        day=day,
        message="coupon_payments_processed",
        context={"processed_bonds": len(ledgers)},
    )

    return updates, ledgers, log


def process_bond_maturities(world_state, tick: int, day: int):
    """处理到期债券：向持有人支付本金+票息，并从政府负债中清算。"""
    government = world_state.government
    updates = []
    ledgers = []
    matured_bonds = []

    # iterate over a copy since we'll modify debt_instruments
    for bond_id, bond in list(government.debt_instruments.items()):
        if bond.maturity_tick <= tick:
            # Determine accrued coupon ticks since last periodic coupon (if any).
            accrued_ticks = 0
            if bond.coupon_frequency_ticks and bond.coupon_frequency_ticks > 0:
                # if next_coupon_tick is set, assume last coupon was at next_coupon_tick - freq
                if bond.next_coupon_tick is not None:
                    last_coupon_tick = (
                        bond.next_coupon_tick - bond.coupon_frequency_ticks
                    )
                else:
                    # no payments yet; assume whole life accrual equals coupon_frequency_ticks
                    last_coupon_tick = bond.maturity_tick - bond.coupon_frequency_ticks

                accrued_ticks = max(0, bond.maturity_tick - last_coupon_tick)
            else:
                # no periodic coupons scheduled: treat coupon_rate as a single maturity coupon
                accrued_ticks = 1

            # coupon per unit = face_value * coupon_rate * accrued_ticks
            coupon_accrued_per_unit = bond.face_value * bond.coupon_rate * accrued_ticks
            payment_per_unit = bond.face_value + coupon_accrued_per_unit

            total_payment = 0.0
            holder_amounts = {}

            # collect bank holders eligible by min-hold rule
            try:
                cfg = get_world_config()
                min_hold_ticks = int(cfg.simulation.ticks_per_day)
            except Exception:
                min_hold_ticks = 1

            def eligible_qty_for_maturity(buyer_id_str: str) -> float:
                if getattr(bond, "purchase_records", None):
                    qty = 0.0
                    for rec in bond.purchase_records:
                        try:
                            if str(rec.get("buyer_id")) == str(buyer_id_str) and int(
                                rec.get("tick", 0)
                            ) <= (tick - min_hold_ticks):
                                qty += float(rec.get("quantity", 0.0))
                        except Exception:
                            continue
                    return qty
                # fallback to aggregate
                if buyer_id_str == (
                    getattr(world_state.bank, "id", None)
                    if getattr(world_state, "bank", None)
                    else None
                ):
                    return (
                        float(
                            getattr(world_state.bank, "bond_holdings", {}).get(
                                bond_id, 0.0
                            )
                        )
                        if getattr(world_state, "bank", None)
                        else 0.0
                    )
                try:
                    hid = int(buyer_id_str)
                    return float(
                        world_state.households[hid].bond_holdings.get(bond_id, 0.0)
                    )
                except Exception:
                    return 0.0

            # bank
            bank = getattr(world_state, "bank", None)
            if bank is not None:
                qty = eligible_qty_for_maturity(bank.id)
                if qty > 0:
                    amt = qty * payment_per_unit
                    holder_amounts[(models.AgentKind.BANK, bank.id)] = (qty, amt)
                    total_payment += amt

            # households
            for hid, hh in getattr(world_state, "households", {}).items():
                qty = eligible_qty_for_maturity(str(hid))
                if qty > 0:
                    amt = qty * payment_per_unit
                    holder_amounts[(models.AgentKind.HOUSEHOLD, str(hid))] = (qty, amt)
                    total_payment += amt

            if total_payment <= 0:
                # nothing to pay; just remove registry and continue
                matured_bonds.append(bond_id)
                del government.debt_instruments[bond_id]
                government.debt_outstanding.pop(bond_id, None)
                continue

            gov_cash = government.balance_sheet.cash
            if gov_cash >= total_payment:
                # full payment
                for (kind, eid), (qty, amt) in holder_amounts.items():
                    if (
                        kind == models.AgentKind.BANK
                        and getattr(world_state, "bank", None) is not None
                        and str(eid) == str(world_state.bank.id)
                    ):
                        bank.balance_sheet.cash += amt
                        bank.bond_holdings[bond_id] = 0.0
                        ledgers.append(
                            models.LedgerEntry(
                                tick=tick,
                                day=day,
                                account_kind=models.AgentKind.BANK,
                                entity_id=bank.id,
                                entry_type="bond_maturity_receipt",
                                amount=amt,
                                balance_after=bank.balance_sheet.cash,
                                reference=bond_id,
                            )
                        )
                        updates.append(
                            models.StateUpdateCommand.assign(
                                scope=models.AgentKind.BANK,
                                agent_id=bank.id,
                                balance_sheet=bank.balance_sheet.model_dump(),
                                bond_holdings=bank.bond_holdings,
                            )
                        )
                    else:
                        hid = int(eid)
                        hh = world_state.households[hid]
                        hh.balance_sheet.cash += amt
                        hh.bond_holdings[bond_id] = 0.0
                        ledgers.append(
                            models.LedgerEntry(
                                tick=tick,
                                day=day,
                                account_kind=models.AgentKind.HOUSEHOLD,
                                entity_id=str(hid),
                                entry_type="bond_maturity_receipt",
                                amount=amt,
                                balance_after=hh.balance_sheet.cash,
                                reference=bond_id,
                            )
                        )
                        updates.append(
                            models.StateUpdateCommand.assign(
                                scope=models.AgentKind.HOUSEHOLD,
                                agent_id=hid,
                                balance_sheet=hh.balance_sheet.model_dump(),
                                bond_holdings=hh.bond_holdings,
                            )
                        )

                # deduct from government
                government.balance_sheet.cash -= total_payment
                ledgers.append(
                    models.LedgerEntry(
                        tick=tick,
                        day=day,
                        account_kind=models.AgentKind.GOVERNMENT,
                        entity_id=government.id,
                        entry_type="bond_maturity_payment",
                        amount=-total_payment,
                        balance_after=government.balance_sheet.cash,
                        reference=bond_id,
                    )
                )
                updates.append(
                    models.StateUpdateCommand.assign(
                        scope=models.AgentKind.GOVERNMENT,
                        agent_id=government.id,
                        balance_sheet=government.balance_sheet.model_dump(),
                        debt_outstanding=government.debt_outstanding,
                    )
                )
            else:
                # partial pro-rata payment
                fraction = gov_cash / total_payment if total_payment > 0 else 0.0
                paid = 0.0
                for (kind, eid), (qty, amt) in holder_amounts.items():
                    pay = amt * fraction
                    paid += pay
                    if (
                        kind == models.AgentKind.BANK
                        and getattr(world_state, "bank", None) is not None
                        and str(eid) == str(world_state.bank.id)
                    ):
                        bank.balance_sheet.cash += pay
                        bank.bond_holdings[bond_id] = 0.0
                        ledgers.append(
                            models.LedgerEntry(
                                tick=tick,
                                day=day,
                                account_kind=models.AgentKind.BANK,
                                entity_id=bank.id,
                                entry_type="bond_maturity_receipt_partial",
                                amount=pay,
                                balance_after=bank.balance_sheet.cash,
                                reference=bond_id,
                            )
                        )
                        updates.append(
                            models.StateUpdateCommand.assign(
                                scope=models.AgentKind.BANK,
                                agent_id=bank.id,
                                balance_sheet=bank.balance_sheet.model_dump(),
                                bond_holdings=bank.bond_holdings,
                            )
                        )
                    else:
                        hid = int(eid)
                        hh = world_state.households[hid]
                        hh.balance_sheet.cash += pay
                        hh.bond_holdings[bond_id] = 0.0
                        ledgers.append(
                            models.LedgerEntry(
                                tick=tick,
                                day=day,
                                account_kind=models.AgentKind.HOUSEHOLD,
                                entity_id=str(hid),
                                entry_type="bond_maturity_receipt_partial",
                                amount=pay,
                                balance_after=hh.balance_sheet.cash,
                                reference=bond_id,
                            )
                        )
                        updates.append(
                            models.StateUpdateCommand.assign(
                                scope=models.AgentKind.HOUSEHOLD,
                                agent_id=hid,
                                balance_sheet=hh.balance_sheet.model_dump(),
                                bond_holdings=hh.bond_holdings,
                            )
                        )

                # deduct paid amount from government
                government.balance_sheet.cash -= paid
                ledgers.append(
                    models.LedgerEntry(
                        tick=tick,
                        day=day,
                        account_kind=models.AgentKind.GOVERNMENT,
                        entity_id=government.id,
                        entry_type="bond_maturity_payment_partial",
                        amount=-paid,
                        balance_after=government.balance_sheet.cash,
                        reference=bond_id,
                    )
                )
                updates.append(
                    models.StateUpdateCommand.assign(
                        scope=models.AgentKind.GOVERNMENT,
                        agent_id=government.id,
                        balance_sheet=government.balance_sheet.model_dump(),
                        debt_outstanding=government.debt_outstanding,
                    )
                )

            # remove bond from registry regardless of full/partial payment
            matured_bonds.append(bond_id)
            del government.debt_instruments[bond_id]
            government.debt_outstanding.pop(bond_id, None)

    log = models.TickLogEntry(
        tick=tick,
        day=day,
        message="bond_maturities_processed",
        context={
            "matured_count": len(matured_bonds),
            "total_paid": sum(
                [l.amount for l in ledgers if l.entry_type == "bond_maturity_receipt"]
            ),
        },
    )

    return updates, ledgers, log
