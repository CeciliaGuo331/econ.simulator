"""简单的债券市场撮合与交易实现（最小化、可扩展）。

接口说明（最小化）：
- clear_bond_auction(world_state, bond, bids, tick, day)
  bids: list of dicts {"buyer_kind": AgentKind, "buyer_id": str|int, "price": float, "quantity": float}

返回值：{"updates": List[StateUpdateCommand], "ledgers": List[LedgerEntry], "trades": List[TradeRecord], "market_price": float, "market_yield": float}

该实现按出价从高到低匹配，按出价成交（价格优先）。
"""

from __future__ import annotations

from typing import List, Dict, Any
from uuid import uuid4
import statistics
import json

from econ_sim.data_access import models
from econ_sim.data_access.models import (
    BondInstrument,
    StateUpdateCommand,
    LedgerEntry,
    TickLogEntry,
    TradeRecord,
    AgentKind,
)
from econ_sim.utils.settings import get_world_config
import math
import random
import logging


def _apply_cash_delta(entity_balance_sheet, delta: float):
    bs = entity_balance_sheet
    bs.cash = bs.cash + delta
    return bs


def clear_bond_auction(
    world_state: models.WorldState,
    bond: BondInstrument,
    bids: List[Dict[str, Any]],
    tick: int,
    day: int,
    min_price: float | None = None,
):
    """按价格优先撮合债券发行（政府卖方）。

    bids: list of {buyer_kind, buyer_id, price, quantity}
    """
    # determine auction mode (price-priority or random-priority per docs)
    cfg = get_world_config()
    auction_mode = "price"
    try:
        auction_mode = cfg.markets.finance.auction_mode
    except Exception:
        auction_mode = "price"

    bids_sorted = list(bids)
    # apply optional reserve / min_price: filter out any bids below min_price
    try:
        if min_price is not None:
            bids_sorted = [
                b for b in bids_sorted if float(b.get("price", 0.0)) >= float(min_price)
            ]
    except Exception:
        # if filtering fails, keep original bids list
        bids_sorted = list(bids)
    if auction_mode == "price":
        # price-priority (existing behavior)
        bids_sorted.sort(key=lambda b: b["price"], reverse=True)
    else:
        # random-priority: deterministic shuffle using global seed + tick
        try:
            seed = int(cfg.simulation.seed or 0) + int(tick)
            rnd = random.Random(seed)
            rnd.shuffle(bids_sorted)
        except Exception:
            # fallback to deterministic sort by buyer id to keep behavior stable
            bids_sorted.sort(key=lambda b: str(b.get("buyer_id", "")))
    remaining = bond.outstanding
    trades: List[TradeRecord] = []
    ledgers: List[LedgerEntry] = []
    updates: List[StateUpdateCommand] = []
    traded_prices = []

    government = world_state.government

    for bid in bids_sorted:
        if remaining <= 0:
            break
        # respect cash constraint: buyer cannot pay more than available cash
        price = float(bid.get("price", 0.0))
        requested_qty = float(bid.get("quantity", 0.0))

        if price <= 0 or requested_qty <= 0:
            continue

        # route buyer object
        buyer_kind_val = bid.get("buyer_kind")
        buyer_id = bid.get("buyer_id")

        # normalize buyer_kind to AgentKind enum when provided as string
        if isinstance(buyer_kind_val, str):
            try:
                buyer_kind_enum = AgentKind(buyer_kind_val)
            except Exception:
                buyer_kind_enum = None
        else:
            buyer_kind_enum = buyer_kind_val

        if buyer_kind_enum == AgentKind.BANK and str(buyer_id) == str(
            world_state.bank.id
        ):
            buyer = world_state.bank
        elif buyer_kind_enum == AgentKind.HOUSEHOLD:
            buyer = world_state.households[int(buyer_id)]
        else:
            # for minimal impl, only support bank and household buyers
            continue

        max_affordable_qty = 0.0
        try:
            max_affordable_qty = (
                float(buyer.balance_sheet.cash) / price if price > 0 else 0.0
            )
        except Exception:
            max_affordable_qty = 0.0

        qty = min(requested_qty, remaining, max_affordable_qty)
        if qty <= 0:
            # buyer cannot afford even a fraction; skip
            continue

        amount = qty * price

        # ensure we pass an AgentKind to finance_market.transfer
        buyer_kind = buyer_kind_enum
        buyer_id = bid["buyer_id"]

        # transfer cash from buyer to government via finance_market
        from . import finance_market

        try:
            t_updates, t_ledgers, t_log = finance_market.transfer(
                world_state,
                payer_kind=buyer_kind,
                payer_id=buyer_id if isinstance(buyer_id, str) else str(buyer_id),
                payee_kind=AgentKind.GOVERNMENT,
                payee_id=government.id,
                amount=amount,
                tick=tick,
                day=day,
            )
        except Exception:
            # If the finance_market.transfer fails, do NOT mutate balance sheets
            # here directly. Mutating persistent state outside the financial
            # subsystem breaks the accounting contract. Log and continue so
            # the failure is observable and can be fixed or retried by the
            # caller.
            logger = logging.getLogger(__name__)
            logger.exception(
                "finance_market.transfer failed during bond auction; cash transfer skipped"
            )
            t_updates, t_ledgers, t_log = [], [], None

        # assign bond holdings (aggregate) and record purchase detail with tick
        buyer.bond_holdings[bond.id] = buyer.bond_holdings.get(bond.id, 0.0) + qty
        bond.holders[str(buyer_id)] = bond.holders.get(str(buyer_id), 0.0) + qty
        government.debt_outstanding[bond.id] = (
            government.debt_outstanding.get(bond.id, 0.0) + qty
        )

        # record detailed purchase record for minimum-hold enforcement
        bond.purchase_records.append(
            {
                "buyer_kind": (
                    buyer_kind.value
                    if isinstance(buyer_kind, AgentKind)
                    else str(buyer_kind)
                ),
                "buyer_id": str(buyer_id),
                "quantity": float(qty),
                "price": float(price),
                "tick": int(tick),
            }
        )

        remaining -= qty
        traded_prices.append(price)

        trades.append(
            TradeRecord(
                tick=tick,
                day=day,
                buyer_kind=buyer_kind,
                buyer_id=str(buyer_id),
                seller_kind=AgentKind.GOVERNMENT,
                seller_id=government.id,
                quantity=qty,
                price=price,
                amount=amount,
            )
        )

        # collect ledgers/updates produced by transfer
        if t_updates:
            updates.extend(t_updates)
        if t_ledgers:
            ledgers.extend(t_ledgers)

        # assign bond holdings and government debt updates (persist these changes)
        if buyer_kind == AgentKind.BANK:
            updates.append(
                StateUpdateCommand.assign(
                    scope=AgentKind.BANK,
                    agent_id=buyer.id,
                    bond_holdings=buyer.bond_holdings,
                )
            )
        else:
            updates.append(
                StateUpdateCommand.assign(
                    scope=AgentKind.HOUSEHOLD,
                    agent_id=buyer.id,
                    bond_holdings=buyer.bond_holdings,
                )
            )

        updates.append(
            StateUpdateCommand.assign(
                scope=AgentKind.GOVERNMENT,
                agent_id=government.id,
                debt_outstanding=government.debt_outstanding,
            )
        )

    # if any remaining (unsold), set outstanding to remaining unsold face value
    sold = bond.outstanding - remaining
    # keep bond.outstanding as original issue amount and register sold quantity in government's debt_outstanding

    market_price = statistics.mean(traded_prices) if traded_prices else None
    market_yield = None
    if market_price and market_price > 0:
        # 计算更标准的到期收益率（YTM），若债券采用周期性 coupon，则按周期求解并年化
        try:
            # determine ticks per year
            cfg = get_world_config()
            ticks_per_year = int(cfg.simulation.ticks_per_day * 365)
        except Exception:
            ticks_per_year = 365

        # determine coupon schedule
        if getattr(bond, "coupon_frequency_ticks", 0):
            freq = int(bond.coupon_frequency_ticks)
            payments_per_year = max(1, int(ticks_per_year / max(1, freq)))
            n_periods = max(1, int(math.ceil((bond.maturity_tick - tick) / freq)))
            # coupon_rate is per-tick rate; coupon per period spanning `freq` ticks equals:
            coupon_per_period = bond.face_value * bond.coupon_rate * freq

            def pv_for_rate(annual_r: float) -> float:
                per_r = annual_r / payments_per_year
                pv = 0.0
                for t in range(1, n_periods + 1):
                    pv += coupon_per_period / ((1 + per_r) ** t)
                pv += bond.face_value / ((1 + per_r) ** n_periods)
                return pv

            # bisection search for annual_r
            low = -0.99
            high = 10.0
            pv_low = pv_for_rate(low)
            pv_high = pv_for_rate(high)
            target = market_price
            ytm = None
            # ensure we have root-bracketing
            if pv_low >= target and pv_high <= target:
                for _ in range(60):
                    mid = (low + high) / 2.0
                    pv_mid = pv_for_rate(mid)
                    if pv_mid > target:
                        low = mid
                    else:
                        high = mid
                ytm = (low + high) / 2.0
            market_yield = ytm
        else:
            # zero-coupon-like treated as single payment at maturity
            market_yield = None

    # register bond in government's debt_instruments registry
    government.debt_instruments[bond.id] = bond

    # produce a TickLogEntry summarizing the auction/trades for persistent market_order_log
    try:
        trades_serial = [t.model_dump() for t in trades]
        log = TickLogEntry(
            tick=tick,
            day=day,
            message="bond_auction_cleared",
            context={
                "bond_id": bond.id,
                "sold": float(sold),
                "market_price": market_price,
                "trades": json.dumps(trades_serial),
            },
        )
    except Exception:
        log = TickLogEntry(
            tick=tick, day=day, message="bond_auction_cleared", context={}
        )

    return {
        "updates": updates,
        "ledgers": ledgers,
        "trades": trades,
        "market_price": market_price,
        "market_yield": market_yield,
        "log": log,
    }
