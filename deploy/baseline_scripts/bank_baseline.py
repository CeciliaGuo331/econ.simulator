"""Baseline commercial bank strategy for Docker deployments."""

from econ_sim.script_engine.user_api import OverridesBuilder, clamp


Context = dict[str, object]
DecisionOverrides = dict[str, object]


def generate_decisions(context: Context) -> DecisionOverrides:
    world = context.get("world_state", {})
    bank = context.get("entity_state") or world.get("bank")
    central_bank = world.get("central_bank", {})

    if not bank:
        return {}

    builder = OverridesBuilder()

    policy_rate = central_bank.get("base_rate", 0.02)
    reserve_ratio = central_bank.get("reserve_ratio", 0.1)
    balance_sheet = bank.get("balance_sheet", {})
    deposits = balance_sheet.get("deposits", 0.0)
    loans = balance_sheet.get("loans", 0.0)
    # compute capital adequacy and apply economics-informed spreads
    # equity approximation: reserves + loans - deposits (BankState.equity exists in models)
    try:
        equity = float(bank.get("equity", 0.0))
    except Exception:
        # fall back to simple calc
        equity = float(
            balance_sheet.get("reserves", 0.0)
            + balance_sheet.get("loans", 0.0)
            - balance_sheet.get("deposits", 0.0)
        )

    capital_adequacy = equity / max(loans, 1.0)
    capital_target = 0.12

    deposit_spread_base = 0.005
    loan_spread_base = 0.025

    # deposit rate falls slightly if capital adequacy is below target
    deposit_rate = clamp(
        policy_rate + deposit_spread_base - 0.5 * (capital_target - capital_adequacy),
        -0.02,
        0.1,
    )

    # loan rate increases when capital adequacy is low to reflect risk premium
    loan_rate = clamp(
        policy_rate
        + loan_spread_base
        + 0.5 * max(0.0, (capital_target - capital_adequacy)),
        0.0,
        0.3,
    )

    # reserve requirement reduces available funds for new loans
    reserve_requirement = reserve_ratio * deposits
    # base loan supply formula from docs: (reserves - reserve_requirement) adjusted by NPL
    non_perf = float(bank.get("non_performing_ratio", 0.03))
    loan_supply = max(
        0.0,
        (balance_sheet.get("reserves", 0.0) - reserve_requirement)
        / max(1.0 + non_perf, 1.0),
    )

    # if capital adequacy below minimum, throttle loan supply
    if capital_adequacy < 0.08:
        loan_supply = 0.0

    builder.bank(
        deposit_rate=round(deposit_rate, 4),
        loan_rate=round(loan_rate, 4),
        loan_supply=round(loan_supply, 2),
    )

    return builder.build()
