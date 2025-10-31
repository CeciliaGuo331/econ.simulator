"""Baseline household strategy used for Docker deployments.

This baseline only uses the household-visible whitelist fields. It is
defensive if `entity_state` is missing (in which case registry should
normally provide it for household scripts)."""

from econ_sim.script_engine.user_api import OverridesBuilder, clamp
import random


def generate_decisions(context: dict) -> dict:
    entity_id_raw = context.get("entity_id")
    if entity_id_raw is None:
        return {}

    try:
        entity_id = int(entity_id_raw)
    except (TypeError, ValueError):
        return {}

    # entity_state for household scripts is expected to be the pruned own-record
    ent = context.get("entity_state") or {}
    if not ent:
        # defensive: do not attempt to read other households; fallback empty
        return {}

    bs = ent.get("balance_sheet", {})
    cash = float(bs.get("cash", 0.0))
    deposits = float(bs.get("deposits", 0.0))
    wage_income = float(ent.get("wage_income", 0.0))

    # Randomized strategy: choose consumption budget and other required
    # fields randomly within sensible/legal bounds. Utility is computed by
    # the engine and depends only on realized consumption.
    liquid = cash + deposits
    max_affordable = max(1.0, liquid + wage_income)

    # consumption: random between minimum and a capped share of resources
    consumption_min = 1.0
    consumption_max = max(1.0, 0.5 * max_affordable)
    consumption = round(random.uniform(consumption_min, consumption_max), 2)

    # savings_rate: between 0 and 0.8
    savings_rate = round(random.uniform(0.0, 0.8), 3)

    features = context.get("world_state", {}).get("features", {}) or {}
    is_daily = bool(features.get("is_daily_decision_tick"))

    # education: only applicable on daily ticks; choose randomly to study
    is_studying = False
    education_payment = 0.0
    if is_daily:
        # increase baseline study probability during dev/test runs so some
        # households actually take education in seeded test_world.
        study_prob = 0.4
        if random.random() < study_prob:
            is_studying = True
            # prefer to pay at least the configured daily education cost when
            # available; fall back to a small fraction of cash capped to a
            # modest amount.
            cfg = context.get("config") or {}
            try:
                cost = float(cfg.get("policies", {}).get("education_cost_per_day", 2.0))
            except Exception:
                cost = 2.0
            education_payment = round(min(max(cost, cash * 0.1), 5.0), 2)

    # labor supply: if studying -> 0.0; otherwise random 0 or 1 (work/no-work)
    if is_studying:
        labor_supply = 0.0
    else:
        labor_supply = 1.0 if random.random() < 0.7 else 0.0

    builder = OverridesBuilder()
    builder.household(
        entity_id,
        consumption_budget=consumption,
        savings_rate=savings_rate,
        labor_supply=labor_supply,
        **(
            {"is_studying": True, "education_payment": education_payment}
            if is_studying
            else {}
        ),
    )
    # small household bond bid participation for baseline smoke tests: households
    # may place a tiny bid (a fraction of cash) to ensure bond auctions have
    # retail-side bids present when seeding test_world.
    try:
        if cash >= 10.0:
            bid_qty = round(min(cash * 0.05, 20.0), 2)
            builder.bond_bids(
                [
                    {
                        "buyer_kind": "household",
                        "buyer_id": entity_id,
                        "price": 1.0,
                        "quantity": bid_qty,
                    }
                ]
            )
    except Exception:
        # best-effort: ignore bidding errors
        pass
    return builder.build()
