import pandas as pd
from typing import Dict, List
from .models import Debt


def bonus_months(mode: str) -> List[int]:
    if mode == "Annual":
        return [12]
    if mode == "Quarterly":
        return [3, 6, 9, 12]
    return []


def debts_monthly_total(debts: List[Debt]) -> float:
    return sum(max(0.0, float(d.monthly_payment)) for d in debts)


def essentials_monthly(
    mortgage: float,
    utilities: float,
    food: float,
    transport: float,
    debts_payment_total: float,
    childcare: float,
) -> float:
    return (
        max(0.0, mortgage)
        + max(0.0, utilities)
        + max(0.0, food)
        + max(0.0, transport)
        + max(0.0, debts_payment_total)
        + max(0.0, childcare)
    )


def emergency_plan(essentials: float, target_months: int, current_balance: float, build_months: int) -> Dict[str, float]:
    """
    Correct approach:
    - target = essentials * target_months
    - gap = max(0, target - current_balance)
    - monthly = gap / build_months
    """
    target = max(0.0, essentials) * max(0, int(target_months))
    gap = max(0.0, target - max(0.0, float(current_balance)))
    months = max(1, int(build_months))
    per_month = gap / months
    return {"target": target, "gap": gap, "per_month": per_month}


def cashflow_12m(
    monthly_base_income: float,
    you_bonus_mode: str,
    you_bonus_amount: float,
    partner_bonus_mode: str,
    partner_bonus_amount: float,
    total_outgoings_monthly: float,
    income_drop_pct: float,
    one_off_cost_month3: float,
) -> pd.DataFrame:
    """
    Builds a 12-month cashflow table.
    - monthly_base_income excludes bonuses
    - bonuses are lumpy (annual in month 12; quarterly in months 3,6,9,12)
    - income_drop_pct reduces base income across all months
    - one_off_cost_month3 adds a one-off outgoing in month 3
    """
    base = max(0.0, float(monthly_base_income)) * (1.0 - (max(0.0, float(income_drop_pct)) / 100.0))
    out_base = max(0.0, float(total_outgoings_monthly))
    one_off = max(0.0, float(one_off_cost_month3))

    y_months = bonus_months(you_bonus_mode)
    p_months = bonus_months(partner_bonus_mode)

    rows = []
    for m in range(1, 13):
        income = base

        if you_bonus_mode in ("Annual", "Quarterly") and m in y_months:
            income += max(0.0, float(you_bonus_amount))

        if partner_bonus_mode in ("Annual", "Quarterly") and m in p_months:
            income += max(0.0, float(partner_bonus_amount))

        out = out_base + (one_off if m == 3 else 0.0)
        rows.append({"month": m, "income": income, "outgoings": out, "net": income - out})

    df = pd.DataFrame(rows)
    df["cumulative"] = df["net"].cumsum()
    return df


def amortisation_schedule(balance: float, apr_pct: float, monthly_payment: float, months: int) -> pd.DataFrame:
    """
    Simple amortisation schedule:
    - interest monthly = balance * (apr/12)
    - principal = payment - interest
    Stops when balance hits 0 or months exhausted.
    """
    bal = max(0.0, float(balance))
    r = (max(0.0, float(apr_pct)) / 100.0) / 12.0
    pay = max(0.0, float(monthly_payment))
    months = max(1, int(months))

    rows = []
    for m in range(1, months + 1):
        if bal <= 0:
            break
        interest = bal * r
        principal = max(0.0, pay - interest)
        principal = min(principal, bal)
        bal -= principal
        rows.append({"month": m, "interest": interest, "principal_paid": principal, "balance": bal})

    return pd.DataFrame(rows)
