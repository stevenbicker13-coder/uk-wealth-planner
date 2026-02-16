import math
import pandas as pd

RISK_PROFILES = {
    "Cautious": {
        "annual_return": 4.0,
        "alloc": {"Global Bonds ETF": 60, "Global Equity ETF": 35, "Cash": 5},
    },
    "Balanced": {
        "annual_return": 6.0,
        "alloc": {"Global Equity ETF": 70, "Global Bonds ETF": 25, "REITs ETF": 5},
    },
    "Growth": {
        "annual_return": 8.0,
        "alloc": {"Global Equity ETF": 85, "Small Cap ETF": 10, "REITs ETF": 5},
    },
}

ETF_SUGGESTIONS = {
    "Global Equity ETF": [
        "VWRP (Vanguard FTSE All-World UCITS)",
        "IWDA (iShares Core MSCI World UCITS)",
    ],
    "Global Bonds ETF": [
        "VAGS (Vanguard Global Aggregate Bond UCITS)",
        "AGBP (iShares Core Global Aggregate Bond UCITS)",
    ],
    "Small Cap ETF": ["WLDS (SPDR MSCI World Small Cap UCITS)"],
    "REITs ETF": ["IWDP (iShares Developed Markets Property Yield UCITS)"],
}


def projection_yearly(start_value: float, monthly_contrib: float, years: int, annual_return_pct: float) -> pd.DataFrame:
    """
    Compound growth model (planner-level):
    - Start at start_value
    - Add monthly_contrib each month
    - Grow with constant annual_return_pct
    Returns end-of-year balances.
    """
    start_value = float(start_value)
    monthly_contrib = float(monthly_contrib)
    years = int(years)
    annual_return_pct = float(annual_return_pct)

    r = (annual_return_pct / 100.0) / 12.0
    months = max(1, years * 12)

    bal = start_value
    rows = []
    for m in range(1, months + 1):
        bal = bal * (1 + r) + monthly_contrib
        rows.append({"month": m, "balance": bal})

    df = pd.DataFrame(rows)
    df["year"] = df["month"].apply(lambda x: int(math.ceil(x / 12.0)))
    return df.groupby("year")["balance"].last().reset_index()
