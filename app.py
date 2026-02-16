import io
import math
import datetime as dt
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd
import streamlit as st

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


# =========================================================
# Tax / Pension configuration (planner-level approximation)
# =========================================================

@dataclass(frozen=True)
class TaxBand:
    upper: Optional[float]
    rate: float

@dataclass(frozen=True)
class TaxConfig:
    personal_allowance: float = 12570.0
    pa_taper_start: float = 100000.0
    pa_taper_end: float = 125140.0

    # Employee NI (simplified annual equivalents)
    ni_primary_threshold: float = 12570.0
    ni_upper_earnings_limit: float = 50270.0
    ni_rate_main: float = 0.08
    ni_rate_additional: float = 0.02

    ruk_bands: Tuple[TaxBand, ...] = (
        TaxBand(upper=50270.0, rate=0.20),
        TaxBand(upper=125140.0, rate=0.40),
        TaxBand(upper=None, rate=0.45),
    )

    scot_bands: Tuple[TaxBand, ...] = (
        TaxBand(upper=15397.0, rate=0.19),
        TaxBand(upper=27491.0, rate=0.20),
        TaxBand(upper=43662.0, rate=0.21),
        TaxBand(upper=75000.0, rate=0.42),
        TaxBand(upper=125140.0, rate=0.45),
        TaxBand(upper=None, rate=0.48),
    )

TAX = TaxConfig()


# Pension annual allowance (planner approximation)
STD_ANNUAL_ALLOWANCE = 60000.0
TAPER_TI = 200000.0   # threshold income (post-2023 regime per common guidance)
TAPER_AI = 260000.0   # adjusted income
TAPER_MIN = 10000.0

MPAA_LIMIT = 10000.0  # money purchase annual allowance, when triggered


# HICBC thresholds (current commonly used levels, see GOV.UK policy background)
HICBC_START = 50000.0
HICBC_END = 60000.0


# =========================================================
# Helpers
# =========================================================

def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

def personal_allowance_for_income(adjusted_net_income: float) -> float:
    if adjusted_net_income <= TAX.pa_taper_start:
        return TAX.personal_allowance
    if adjusted_net_income >= TAX.pa_taper_end:
        return 0.0
    reduction = (adjusted_net_income - TAX.pa_taper_start) / 2.0
    return max(0.0, TAX.personal_allowance - reduction)

def income_tax_due_employment_only(gross: float, pension_salary_sacrifice: float, is_scotland: bool) -> float:
    adjusted_net = max(0.0, gross - pension_salary_sacrifice)
    pa = personal_allowance_for_income(adjusted_net)
    taxable = max(0.0, adjusted_net - pa)

    bands = TAX.scot_bands if is_scotland else TAX.ruk_bands

    tax_due = 0.0
    lower_taxable = 0.0
    remaining = taxable

    for band in bands:
        if remaining <= 0:
            break

        if band.upper is None:
            slice_amt = remaining
        else:
            taxable_upper = max(0.0, band.upper - pa)
            slice_amt = max(0.0, min(remaining, taxable_upper - lower_taxable))

        if slice_amt > 0:
            tax_due += slice_amt * band.rate
            remaining -= slice_amt
            lower_taxable += slice_amt

    return tax_due

def employee_ni_due(gross: float, pension_salary_sacrifice: float) -> float:
    ni_pay = max(0.0, gross - pension_salary_sacrifice)
    pt = TAX.ni_primary_threshold
    uel = TAX.ni_upper_earnings_limit

    if ni_pay <= pt:
        return 0.0

    main_slice = min(ni_pay, uel) - pt
    addl_slice = max(0.0, ni_pay - uel)

    return max(0.0, main_slice) * TAX.ni_rate_main + addl_slice * TAX.ni_rate_additional

def net_employment_takehome(gross: float, pension_salary_sacrifice: float, is_scotland: bool) -> Dict[str, float]:
    pension_salary_sacrifice = clamp(pension_salary_sacrifice, 0.0, gross)
    tax = income_tax_due_employment_only(gross, pension_salary_sacrifice, is_scotland)
    ni = employee_ni_due(gross, pension_salary_sacrifice)
    take_home = max(0.0, gross - pension_salary_sacrifice - tax - ni)
    return {"gross": gross, "pension": pension_salary_sacrifice, "tax": tax, "ni": ni, "take_home": take_home}

def annual_from_percent(base: float, pct: float) -> float:
    return base * (pct / 100.0)

def make_pdf_report(lines: list[str]) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4

    y = height - 50
    c.setFont("Helvetica-Bold", 16)
    c.drawString(40, y, "UK Wealth Planner — Report")
    y -= 24

    c.setFont("Helvetica", 10)
    c.drawString(40, y, f"Generated: {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    y -= 24

    c.setFont("Helvetica", 11)
    for line in lines:
        if y < 60:
            c.showPage()
            y = height - 50
            c.setFont("Helvetica", 11)
        c.drawString(40, y, line[:115])
        y -= 16

    c.showPage()
    c.save()
    buf.seek(0)
    return buf.read()

def hicbc_charge(adjusted_net_income: float, child_benefit_annual: float) -> float:
    if child_benefit_annual <= 0:
        return 0.0
    if adjusted_net_income <= HICBC_START:
        return 0.0
    if adjusted_net_income >= HICBC_END:
        return child_benefit_annual
    pct = (adjusted_net_income - HICBC_START) / (HICBC_END - HICBC_START)  # 0..1
    return child_benefit_annual * pct

def annual_child_benefit(children: int) -> float:
    # Planner approximation; update annually.
    if children <= 0:
        return 0.0
    first = 25.60
    additional = 16.95
    weekly = first + max(0, children - 1) * additional
    return weekly * 52

def months_until(date_obj: dt.date) -> int:
    today = dt.date.today()
    if date_obj <= today:
        return 0
    return (date_obj.year - today.year) * 12 + (date_obj.month - today.month)

def amortisation_schedule(balance: float, annual_rate: float, monthly_payment: float, months: int) -> pd.DataFrame:
    r = (annual_rate / 100.0) / 12.0
    bal = balance
    rows = []
    for m in range(1, months + 1):
        if bal <= 0:
            break
        interest = bal * r
        principal_paid = max(0.0, monthly_payment - interest)
        principal_paid = min(principal_paid, bal)
        bal -= principal_paid
        rows.append({"month": m, "interest": interest, "principal_paid": principal_paid, "balance": bal})
    return pd.DataFrame(rows)

def compound_growth(lump_sum: float, monthly_contrib: float, years: int, annual_return_pct: float) -> pd.DataFrame:
    r = (annual_return_pct / 100.0) / 12.0
    months = years * 12
    bal = lump_sum
    rows = []
    for m in range(1, months + 1):
        bal = bal * (1 + r) + monthly_contrib
        rows.append({"month": m, "balance": bal})
    df = pd.DataFrame(rows)
    df["year"] = (df["month"] / 12.0).apply(math.ceil)
    return df.groupby("year")["balance"].last().reset_index()

def pension_annual_allowance_estimate(
    employment_gross: float,
    employee_contrib: float,
    employer_contrib: float,
    mpaa_triggered: bool
) -> float:
    """
    Planner-level AA:
    - Standard 60k
    - If both threshold income > 200k and adjusted income > 260k -> taper £1 per £2 over 260k to min 10k
    - If MPAA triggered -> cap at 10k
    - Not modelling carry-forward.
    """
    # Very simplified proxies:
    threshold_income = max(0.0, employment_gross - employee_contrib)
    adjusted_income = max(0.0, employment_gross + employer_contrib)

    aa = STD_ANNUAL_ALLOWANCE

    if threshold_income > TAPER_TI and adjusted_income > TAPER_AI:
