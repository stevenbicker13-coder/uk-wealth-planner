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
        reduction = (adjusted_income - TAPER_AI) / 2.0
        aa = max(TAPER_MIN, STD_ANNUAL_ALLOWANCE - reduction)

    if mpaa_triggered:
        aa = min(aa, MPAA_LIMIT)

    # Separate rule: personal contributions for relief usually limited to 100% of earnings
    aa = min(aa, employment_gross)

    return max(0.0, aa)


# =========================================================
# Investing profiles + assumed return
# =========================================================

RISK_PROFILES = {
    "Cautious": {"annual_return": 4.0, "alloc": {"Global Bonds ETF": 60, "Global Equity ETF": 35, "Cash": 5}},
    "Balanced": {"annual_return": 6.0, "alloc": {"Global Equity ETF": 70, "Global Bonds ETF": 25, "REITs ETF": 5}},
    "Growth":   {"annual_return": 8.0, "alloc": {"Global Equity ETF": 85, "Small Cap ETF": 10, "REITs ETF": 5}},
}

ETF_SUGGESTIONS = {
    "Global Equity ETF": ["VWRP (Vanguard FTSE All-World UCITS)", "IWDA (iShares Core MSCI World UCITS)"],
    "Global Bonds ETF": ["VAGS (Vanguard Global Aggregate Bond UCITS)", "AGBP (iShares Core Global Aggregate Bond UCITS)"],
    "Small Cap ETF": ["WLDS (SPDR MSCI World Small Cap UCITS)"],
    "REITs ETF": ["IWDP (iShares Developed Markets Property Yield UCITS)"],
}


# =========================================================
# App UI
# =========================================================

st.set_page_config(page_title="UK Wealth Planner", layout="wide")
st.title("UK Wealth Planner — Enhanced")
st.caption("Planner tool only (not financial/tax advice). Childcare/tax rules vary and can change.")

with st.sidebar:
    st.header("Household")

    nation = st.selectbox(
        "Where do you pay income tax?",
        ["England/Wales/Northern Ireland", "Scotland"],
        help="Used for income tax banding and childcare scheme modelling (England vs Scotland differ)."
    )
    is_scotland = nation == "Scotland"

    has_partner = st.checkbox("Include partner", value=True, help="Include a second adult's income, pensions and childcare eligibility checks.")
    children_count = st.number_input("Number of children", min_value=0, max_value=10, value=1, step=1, help="Used for Child Benefit and childcare modelling.")

    mpaa_triggered = st.checkbox(
        "Have you flexibly accessed a defined contribution pension (MPAA may apply)?",
        value=False,
        help="If yes, your pension annual allowance for DC contributions can be reduced (commonly £10,000)."
    )

    st.divider()
    st.header("Income")

    st.subheader("You")
    you_salary = st.number_input("Salary (£/year)", min_value=0.0, value=65000.0, step=1000.0, help="Gross annual salary (before tax).")

    you_bonus_mode = st.radio(
        "Bonus frequency",
        ["None", "Annual", "Quarterly"],
        horizontal=True,
        help="Bonus is included for annual tax calculation, but only appears in monthly cashflow in the month(s) it's paid."
    )
    if you_bonus_mode == "None":
        you_bonus_annual = 0.0
    elif you_bonus_mode == "Annual":
        you_bonus_annual = st.number_input("Annual bonus (£)", min_value=0.0, value=5000.0, step=500.0, help="Bonus paid once per year (cashflow assumes month 12).")
    else:
        you_bonus_q = st.number_input("Bonus per quarter (£)", min_value=0.0, value=1500.0, step=250.0, help="Paid in months 3, 6, 9, 12 (cashflow).")
        you_bonus_annual = you_bonus_q * 4.0

    # Pension inputs
    you_gross_employment = you_salary + you_bonus_annual

    st.markdown("**Pension contributions (you)**")
    you_pension_mode = st.radio("Employee contribution type", ["% of salary", "£ per year"], horizontal=True, help="Used to estimate taxable pay (salary sacrifice-style) and pension annual allowance usage.")
    if you_pension_mode.startswith("%"):
        you_pension_pct = st.number_input("Employee pension %", min_value=0.0, max_value=100.0, value=6.0, step=0.5)
        you_pension_employee = annual_from_percent(you_salary, you_pension_pct)
    else:
        you_pension_employee = st.number_input("Employee pension (£/year)", min_value=0.0, value=6000.0, step=250.0)

    you_employer_mode = st.radio("Employer contribution type", ["% of salary", "£ per year"], horizontal=True, help="Employer pension counts toward annual allowance for most people.")
    if you_employer_mode.startswith("%"):
        you_employer_pct = st.number_input("Employer pension %", min_value=0.0, max_value=100.0, value=3.0, step=0.5)
        you_pension_employer = annual_from_percent(you_salary, you_employer_pct)
    else:
        you_pension_employer = st.number_input("Employer pension (£/year)", min_value=0.0, value=3000.0, step=250.0)

    partner_salary = partner_bonus_annual = partner_pension_employee = partner_pension_employer = 0.0
    partner_bonus_mode = "None"
    if has_partner:
        st.subheader("Partner")
        partner_salary = st.number_input("Partner salary (£/year)", min_value=0.0, value=42000.0, step=1000.0)

        partner_bonus_mode = st.radio("Partner bonus frequency", ["None", "Annual", "Quarterly"], horizontal=True)
        if partner_bonus_mode == "None":
            partner_bonus_annual = 0.0
        elif partner_bonus_mode == "Annual":
            partner_bonus_annual = st.number_input("Partner annual bonus (£)", min_value=0.0, value=0.0, step=500.0)
        else:
            p_bonus_q = st.number_input("Partner bonus per quarter (£)", min_value=0.0, value=0.0, step=250.0)
            partner_bonus_annual = p_bonus_q * 4.0

        partner_gross_employment = partner_salary + partner_bonus_annual

        st.markdown("**Pension contributions (partner)**")
        p_mode = st.radio("Partner employee contribution type", ["% of salary", "£ per year"], horizontal=True, key="p_emp_type")
        if p_mode.startswith("%"):
            p_pct = st.number_input("Partner employee pension %", min_value=0.0, max_value=100.0, value=5.0, step=0.5)
            partner_pension_employee = annual_from_percent(partner_salary, p_pct)
        else:
            partner_pension_employee = st.number_input("Partner employee pension (£/year)", min_value=0.0, value=3000.0, step=250.0)

        p_empr_mode = st.radio("Partner employer contribution type", ["% of salary", "£ per year"], horizontal=True, key="p_empr_type")
        if p_empr_mode.startswith("%"):
            p_empr_pct = st.number_input("Partner employer pension %", min_value=0.0, max_value=100.0, value=3.0, step=0.5)
            partner_pension_employer = annual_from_percent(partner_salary, p_empr_pct)
        else:
            partner_pension_employer = st.number_input("Partner employer pension (£/year)", min_value=0.0, value=2000.0, step=250.0)
    else:
        partner_gross_employment = 0.0

    st.divider()
    st.header("Savings interest & other income")

    savings_total = st.number_input(
        "Total savings balance (£)",
        min_value=0.0,
        value=10000.0,
        step=500.0,
        help="Used to estimate annual interest income."
    )
    savings_rate = st.number_input(
        "Average savings interest rate (%)",
        min_value=0.0,
        value=4.0,
        step=0.1,
        help="Estimate across all savings accounts."
    )
    savings_interest_annual = savings_total * (savings_rate / 100.0)

    dividends_annual = st.number_input("Dividends (£/year)", min_value=0.0, value=0.0, step=100.0, help="Planner input. Tax treatment is different from employment income.")
    rental_annual = st.number_input("Rental net income (£/year)", min_value=0.0, value=0.0, step=250.0, help="Net after allowable costs (planner input).")

    st.divider()
    st.header("Mortgage")
    mortgage_payment = st.number_input("Mortgage payment (£/mo)", min_value=0.0, value=1600.0, step=50.0, help="Monthly mortgage payment (used for budget and amortisation).")
    mortgage_balance = st.number_input("Mortgage balance (£)", min_value=0.0, value=300000.0, step=5000.0)
    mortgage_rate = st.number_input("Mortgage interest rate (%)", min_value=0.0, value=4.5, step=0.1)
    mortgage_term_years = st.number_input("Mortgage term remaining (years)", min_value=1, value=25, step=1)

    st.divider()
    st.header("Other debts (multiple)")
    st.caption("Add car finance, loans, credit cards etc. End date helps estimate remaining months.")
    default_debts = pd.DataFrame([
        {"name": "Car finance", "balance": 8000.0, "apr_pct": 9.9, "monthly_payment": 250.0, "end_date": ""},
    ])
    debts_df = st.data_editor(
        default_debts,
        num_rows="dynamic",
        use_container_width=True,
        help="Enter each debt. If end_date is provided (YYYY-MM-DD), we use it to estimate months remaining."
    )

    st.divider()
    st.header("Spending (monthly)")
    food = st.number_input("Food & groceries (£/mo)", min_value=0.0, value=650.0, step=25.0)
    leisure = st.number_input("Leisure (£/mo)", min_value=0.0, value=450.0, step=25.0)
    transport = st.number_input("Transport (£/mo)", min_value=0.0, value=350.0, step=25.0)
    utilities = st.number_input("Utilities (£/mo)", min_value=0.0, value=320.0, step=25.0)
    subscriptions = st.number_input("Subscriptions (£/mo)", min_value=0.0, value=55.0, step=5.0)
    shopping = st.number_input("Shopping/misc (£/mo)", min_value=0.0, value=250.0, step=25.0)

    st.divider()
    st.header("Holidays")
    holiday_cost_each = st.number_input("Cost per family holiday (£)", min_value=0.0, value=2500.0, step=100.0)
    holidays_per_year = st.number_input("Holidays per year", min_value=0, max_value=6, value=2, step=1)
    holiday_sinking_monthly = (holiday_cost_each * holidays_per_year) / 12.0

    st.divider()
    st.header("Emergency fund")
    emergency_months = st.slider(
        "Emergency fund target (months of essentials)",
        0, 12, 3,
        help="Common guidance is 3–6 months. You can change this."
    )
    emergency_current = st.number_input(
        "Current emergency fund balance (£)",
        min_value=0.0,
        value=0.0,
        step=250.0,
        help="How much you already have set aside."
    )
    emergency_build_months = st.slider(
        "Build the remaining gap over (months)",
        1, 36, 12,
        help="We amortise your remaining emergency fund gap over this timeframe."
    )

    st.divider()
    st.header("Childcare / nursery (per child)")
    st.caption("Planner estimates only. England vs Scotland differ. The £100k per-parent limit affects some England schemes.")
    if children_count > 0:
        children_rows = []
        for i in range(int(children_count)):
            st.markdown(f"**Child {i+1}**")
            age_years = st.number_input(f"Age (years) — child {i+1}", min_value=0.0, max_value=18.0, value=3.0, step=0.25,
                                       help="Used to estimate funded hours and Tax-Free Childcare eligibility.")
            days_per_week = st.number_input(f"Nursery days/week — child {i+1}", min_value=0.0, max_value=7.0, value=3.0, step=0.5,
                                           help="How many days they attend each week.")
            hours_per_day = st.number_input(f"Hours/day — child {i+1}", min_value=0.0, max_value=12.0, value=10.0, step=0.5,
                                           help="Used to convert funded hours into £ value.")
            cost_per_day = st.number_input(f"Cost per day (£) — child {i+1}", min_value=0.0, value=70.0, step=5.0,
                                          help="Typical daily fee. We derive an hourly cost using hours/day.")
            children_rows.append({"age_years": age_years, "days_per_week": days_per_week, "hours_per_day": hours_per_day, "cost_per_day": cost_per_day})
        children_df = pd.DataFrame(children_rows)
    else:
        children_df = pd.DataFrame(columns=["age_years", "days_per_week", "hours_per_day", "cost_per_day"])

    st.divider()
    st.header("Scenarios")
    rate_rise = st.slider("Interest rate shock (+% points)", 0.0, 5.0, 1.0, 0.25,
                          help="Adds to mortgage APR and debt APR to stress test repayments and balances.")
    income_drop_pct = st.slider("Income reduction (%)", 0, 50, 0, 5,
                                help="Applies a % drop to monthly base pay (not bonuses) in the cashflow timeline.")
    one_off_cost = st.number_input("One-off cost in month 3 (£)", min_value=0.0, value=0.0, step=100.0,
                                   help="E.g., car repair, boiler replacement, tax bill. Added in month 3.")

    st.divider()
    st.header("Investing")
    risk_profile = st.select_slider("Risk profile", options=list(RISK_PROFILES.keys()), value="Balanced",
                                    help="Used to suggest a broad allocation and an assumed long-run return for projections.")
    invest_monthly_target = st.number_input("Monthly investing target (£/mo)", min_value=0.0, value=500.0, step=25.0,
                                           help="How much you plan to invest monthly going forward.")
    invested_existing = st.number_input("Already invested (£)", min_value=0.0, value=10000.0, step=500.0,
                                        help="Existing portfolio value invested for the long term.")
    invest_lump_sum = st.number_input("Extra lump sum available to invest (£)", min_value=0.0, value=0.0, step=500.0,
                                     help="Cash you could invest now (in addition to what’s already invested).")
    invest_years = st.number_input("Years to invest", min_value=1, value=10, step=1,
                                   help="Investment horizon for the projection chart.")


# =========================================================
# Derived calculations
# =========================================================

you_employment_gross = you_salary + you_bonus_annual
partner_employment_gross = partner_salary + partner_bonus_annual

# Pension annual allowance enforcement
you_aa = pension_annual_allowance_estimate(you_employment_gross, you_pension_employee, you_pension_employer, mpaa_triggered)
you_max_employee = max(0.0, you_aa - you_pension_employer)
if you_pension_employee > you_max_employee:
    you_pension_employee = you_max_employee

partner_aa = 0.0
partner_max_employee = 0.0
if has_partner:
    partner_aa = pension_annual_allowance_estimate(partner_employment_gross, partner_pension_employee, partner_pension_employer, mpaa_triggered)
    partner_max_employee = max(0.0, partner_aa - partner_pension_employer)
    if partner_pension_employee > partner_max_employee:
        partner_pension_employee = partner_max_employee

# Employment take-home (bonus included for annual tax, but cashflow handles timing separately)
you_net = net_employment_takehome(you_employment_gross, you_pension_employee, is_scotland=is_scotland)
partner_net = None
if has_partner:
    partner_net = net_employment_takehome(partner_employment_gross, partner_pension_employee, is_scotland=is_scotland)

# Child Benefit + HICBC (based on highest adjusted net income proxy)
cb_annual = annual_child_benefit(int(children_count))
you_adj_net_proxy = max(0.0, you_employment_gross - you_pension_employee)
partner_adj_net_proxy = max(0.0, partner_employment_gross - partner_pension_employee) if has_partner else 0.0
highest_adj = max(you_adj_net_proxy, partner_adj_net_proxy)
hicbc_annual = hicbc_charge(highest_adj, cb_annual)

# Savings interest + other income (planner)
other_income_annual = savings_interest_annual + dividends_annual + rental_annual - hicbc_annual

# Total household annual cash available (employment takehome + other income)
employment_takehome_annual = you_net["take_home"] + (partner_net["take_home"] if partner_net else 0.0)
household_takehome_annual = max(0.0, employment_takehome_annual + other_income_annual)

# =========================================================
# Childcare modelling (planner approximation)
# =========================================================

def england_working_parent_eligible(highest_parent_adj: float) -> bool:
    # If either parent earns >100k adjusted net income -> not eligible
    return highest_parent_adj <= 100000.0

def tax_free_childcare_eligible(highest_parent_adj: float, child_age_years: float) -> bool:
    # Simplified: same 100k cap proxy; age under ~11 is typical eligibility window
    return highest_parent_adj <= 100000.0 and child_age_years < 11.0

def funded_hours_england(child_age_years: float, eligible_working_parent: bool) -> float:
    """
    Very simplified, based on GOV.UK guidance that funded childcare is available
    for working parents from 9 months to 4 years old, and universal 15 hours for 3-4.
    We assume 'working' requirement satisfied; we only model the £100k per-parent cap.
    """
    if child_age_years >= 3.0 and child_age_years < 5.0:
        # universal 15 hours; if eligible, working parent offer can be higher
        return 30.0 if eligible_working_parent else 15.0
    if child_age_years >= 0.75 and child_age_years < 3.0:
        return 30.0 if eligible_working_parent else 0.0
    return 0.0

def funded_hours_scotland(child_age_years: float) -> float:
    """
    Scotland: funded ELC for 3-4 (and some 2s), commonly expressed as 1140 hours/year.
    We'll convert to weekly assuming 38 weeks*30 hours = 1140 as a rough equivalence.
    """
    if child_age_years >= 3.0 and child_age_years < 5.0:
        return 30.0
    return 0.0

childcare_gross_annual = 0.0
childcare_funded_value_annual = 0.0
childcare_tfc_topup_annual = 0.0

if not children_df.empty:
    eligible_eng = england_working_parent_eligible(highest_adj) if not is_scotland else False

    for _, row in children_df.iterrows():
        age = float(row["age_years"])
        days = float(row["days_per_week"])
        hrs_day = max(0.0, float(row["hours_per_day"]))
        cost_day = max(0.0, float(row["cost_per_day"]))

        weeks = 52.0
        annual_days = days * weeks
        gross = annual_days * cost_day
        childcare_gross_annual += gross

        # hourly cost
        cost_hr = (cost_day / hrs_day) if hrs_day > 0 else 0.0
        attendance_hours_week = days * hrs_day

        if is_scotland:
            fh = funded_hours_scotland(age)
        else:
            fh = funded_hours_england(age, eligible_eng)

        funded_hours_used = min(fh, attendance_hours_week)
        funded_value = funded_hours_used * cost_hr * 38.0  # funded hours are typically term-time based (38 weeks)
        childcare_funded_value_annual += max(0.0, funded_value)

        # Tax-Free Childcare top-up (UK-wide) – planner approximation: 20% of net costs capped at £2k/child/yr
        tfc_ok = tax_free_childcare_eligible(highest_adj, age)
        net_cost_after_funded = max(0.0, gross - funded_value)
        if tfc_ok:
            childcare_tfc_topup_annual += min(net_cost_after_funded * 0.20, 2000.0)

childcare_net_annual = max(0.0, childcare_gross_annual - childcare_funded_value_annual - childcare_tfc_topup_annual)
childcare_net_monthly = childcare_net_annual / 12.0

# =========================================================
# Multiple debts: budget + schedules
# =========================================================

def parse_end_date(x: str) -> Optional[dt.date]:
    try:
        return dt.date.fromisoformat(str(x).strip())
    except Exception:
        return None

other_debt_monthly_total = 0.0
debt_schedules = []

if isinstance(debts_df, pd.DataFrame) and not debts_df.empty:
    for _, r in debts_df.iterrows():
        name = str(r.get("name", "Debt")).strip() or "Debt"
        balance = float(r.get("balance", 0.0) or 0.0)
        apr = float(r.get("apr_pct", 0.0) or 0.0)
        pay = float(r.get("monthly_payment", 0.0) or 0.0)
        end_date = parse_end_date(r.get("end_date", ""))

        if pay > 0:
            other_debt_monthly_total += pay

        # months remaining
        m_rem = months_until(end_date) if end_date else 120
        m_rem = max(1, min(m_rem, 360))

        sched = amortisation_schedule(balance, apr + rate_rise, pay, m_rem)
        if not sched.empty:
            sched["debt"] = name
            debt_schedules.append(sched)

debts_sched_df = pd.concat(debt_schedules, ignore_index=True) if debt_schedules else pd.DataFrame()

# Mortgage schedule under rate shock
mort_sched = amortisation_schedule(
    mortgage_balance,
    mortgage_rate + rate_rise,
    mortgage_payment,
    months=int(mortgage_term_years * 12)
)

# =========================================================
# Budget + emergency fund correction
# =========================================================

expenses = {
    "mortgage": mortgage_payment,
    "debts": other_debt_monthly_total,
    "childcare": childcare_net_monthly,
    "food": food,
    "leisure": leisure,
    "transport": transport,
    "utilities": utilities,
    "subscriptions": subscriptions,
    "shopping": shopping,
}

# Essentials: mortgage + utilities + food + transport + debts + childcare (you can argue childcare is discretionary vs essential; here we include it)
essentials = expenses["mortgage"] + expenses["utilities"] + expenses["food"] + expenses["transport"] + expenses["debts"] + expenses["childcare"]

emergency_target = essentials * emergency_months
emergency_gap = max(0.0, emergency_target - emergency_current)
emergency_monthly_needed = emergency_gap / emergency_build_months if emergency_build_months > 0 else emergency_gap

goals = {
    "holidays": holiday_sinking_monthly,
    "investing": invest_monthly_target,
    "emergency_build": emergency_monthly_needed,
}

# Monthly base take-home excluding bonus timing (we’ll put bonus into specific months)
base_monthly_income = (household_takehome_annual - (you_bonus_annual + partner_bonus_annual)) / 12.0
base_monthly_income = max(0.0, base_monthly_income) * (1 - income_drop_pct / 100.0)

total_outgoings_monthly = sum(expenses.values()) + sum(goals.values())

# =========================================================
# Cashflow timeline (12 months) with bonus timing
# =========================================================

def bonus_months(mode: str):
    if mode == "Annual":
        return [12]
    if mode == "Quarterly":
        return [3, 6, 9, 12]
    return []

you_bonus_months = bonus_months(you_bonus_mode)
partner_bonus_months = bonus_months(partner_bonus_mode) if has_partner else []

timeline = []
for m in range(1, 13):
    inc = base_monthly_income

    # Add bonus only when paid
    if you_bonus_mode == "Annual" and m in you_bonus_months:
        inc += you_bonus_annual
    elif you_bonus_mode == "Quarterly" and m in you_bonus_months:
        inc += (you_bonus_annual / 4.0)

    if has_partner:
        if partner_bonus_mode == "Annual" and m in partner_bonus_months:
            inc += partner_bonus_annual
        elif partner_bonus_mode == "Quarterly" and m in partner_bonus_months:
            inc += (partner_bonus_annual / 4.0)

    out = total_outgoings_monthly
    if m == 3 and one_off_cost > 0:
        out += one_off_cost

    timeline.append({"month": m, "income": inc, "outgoings": out, "net": inc - out})

timeline_df = pd.DataFrame(timeline)
timeline_df["cumulative"] = timeline_df["net"].cumsum()

# =========================================================
# Investing projection
# =========================================================

risk = RISK_PROFILES[risk_profile]
annual_return = float(risk["annual_return"])
alloc = risk["alloc"]

invest_start = invested_existing + invest_lump_sum
invest_proj = compound_growth(invest_start, invest_monthly_target, int(invest_years), annual_return)

# =========================================================
# Display
# =========================================================

left, right = st.columns([1.25, 0.75], gap="large")

with left:
    st.subheader("Income & pensions")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**You**")
        st.write(f"Employment gross (incl bonus): £{you_net['gross']:,.0f}/yr")
        st.write(f"Employee pension used: £{you_pension_employee:,.0f}/yr")
        st.write(f"Employer pension: £{you_pension_employer:,.0f}/yr")
        st.write(f"Income Tax (est): £{you_net['tax']:,.0f}/yr")
        st.write(f"NI (est): £{you_net['ni']:,.0f}/yr")
        st.success(f"Employment take-home: £{you_net['take_home']:,.0f}/yr")

        st.caption(f"Pension AA estimate: £{you_aa:,.0f}/yr → max employee (after employer): £{you_max_employee:,.0f}/yr")

    with c2:
        if partner_net:
            st.markdown("**Partner**")
            st.write(f"Employment gross (incl bonus): £{partner_net['gross']:,.0f}/yr")
            st.write(f"Employee pension used: £{partner_pension_employee:,.0f}/yr")
            st.write(f"Employer pension: £{partner_pension_employer:,.0f}/yr")
            st.write(f"Income Tax (est): £{partner_net['tax']:,.0f}/yr")
            st.write(f"NI (est): £{partner_net['ni']:,.0f}/yr")
            st.success(f"Employment take-home: £{partner_net['take_home']:,.0f}/yr")
            st.caption(f"Pension AA estimate: £{partner_aa:,.0f}/yr → max employee (after employer): £{partner_max_employee:,.0f}/yr")
        else:
            st.info("Partner not included.")

    if (you_pension_mode and you_pension_employee == you_max_employee and you_pension_employee > 0) or (has_partner and partner_pension_employee == partner_max_employee and partner_pension_employee > 0):
        st.warning("Employee pension input was capped by the estimated annual allowance after employer contributions (planner approximation).")

    st.divider()
    st.subheader("Child Benefit & other income")

    st.write(f"Child Benefit (annual): £{cb_annual:,.0f}")
    st.write(f"HICBC estimate (annual charge): £{hicbc_annual:,.0f}")
    st.write(f"Savings interest estimate: £{savings_interest_annual:,.0f}/yr")
    st.write(f"Dividends: £{dividends_annual:,.0f}/yr | Rental net: £{rental_annual:,.0f}/yr")

    st.divider()
    st.subheader("Childcare / nursery estimate")

    if children_count == 0:
        st.info("No children entered.")
    else:
        st.write(f"Gross nursery cost (annual): £{childcare_gross_annual:,.0f}")
        st.write(f"Funded hours value (annual): £{childcare_funded_value_annual:,.0f}")
        st.write(f"Tax-Free Childcare top-up (annual): £{childcare_tfc_topup_annual:,.0f}")
        st.success(f"Net childcare cost (annual): £{childcare_net_annual:,.0f}  →  £{childcare_net_monthly:,.0f}/mo")

        if not is_scotland and highest_adj > 100000:
            st.warning("England scheme note: because a parent is over £100k (proxy), the app assumes NOT eligible for Tax-Free Childcare and the working-parent funded offer.")
        if is_scotland:
            st.caption("Scotland note: funded ELC is modelled for 3–4 year olds (1140 hours/year equivalent).")

    st.divider()
    st.subheader("Monthly budget")

    monthly_income_display = household_takehome_annual / 12.0  # blended monthly average (even though bonus is lumpy)
    k1, k2, k3 = st.columns(3)
    k1.metric("Net income (avg monthly)", f"£{monthly_income_display:,.0f}")
    k2.metric("Total outgoings (monthly)", f"£{total_outgoings_monthly:,.0f}")
    k3.metric("Surplus / deficit (avg)", f"£{(monthly_income_display - total_outgoings_monthly):,.0f}")

    st.caption("Cashflow timeline below shows bonus timing (lumpy months) and scenario impacts.")

    st.divider()
    st.subheader("Emergency fund (corrected)")

    st.write(f"Essentials (monthly): £{essentials:,.0f}")
    st.write(f"Emergency fund target ({emergency_months} months): £{emergency_target:,.0f}")
    st.write(f"Current emergency fund: £{emergency_current:,.0f}")
    st.success(f"Remaining gap: £{emergency_gap:,.0f} → save about **£{emergency_monthly_needed:,.0f}/mo** for {emergency_build_months} months")

    st.divider()
    st.subheader("Cashflow timeline (12 months)")

    st.dataframe(timeline_df, use_container_width=True)
    st.line_chart(timeline_df.set_index("month")[["net", "cumulative"]])

    st.divider()
    st.subheader("Debt amortisation (rate shock applied)")

    st.caption(f"Mortgage APR used: {(mortgage_rate + rate_rise):.2f}%  |  Debt APRs increased by +{rate_rise:.2f} points")

    if not mort_sched.empty:
        st.write("**Mortgage (first 12 months)**")
        st.dataframe(mort_sched.head(12), use_container_width=True)
        st.line_chart(mort_sched.set_index("month")[["balance"]].head(120))

    if not debts_sched_df.empty:
        st.write("**Other debts (first 12 months per debt)**")
        for debt_name in debts_sched_df["debt"].unique():
            ddf = debts_sched_df[debts_sched_df["debt"] == debt_name].copy()
            st.markdown(f"**{debt_name}**")
            st.dataframe(ddf.head(12), use_container_width=True)
            st.line_chart(ddf.set_index("month")[["balance"]].head(60))


with right:
    st.subheader("Investing")

    alloc_df = pd.DataFrame({"Bucket": list(alloc.keys()), "Percent": list(alloc.values())})
    st.dataframe(alloc_df, use_container_width=True)

    # Pie chart (matplotlib object from pandas)
    st.pyplot(alloc_df.set_index("Bucket").plot.pie(y="Percent", legend=False, ylabel="").figure)

    st.markdown("**Example ETFs (swap for your preferred UCITS list):**")
    for bucket, pct in alloc.items():
        examples = ETF_SUGGESTIONS.get(bucket, [])
        if examples:
            st.write(f"- {bucket} ({pct}%): " + "; ".join(examples))

    st.divider()
    st.subheader("Investment forecast")

    st.write(f"Assumed long-run return for **{risk_profile}**: ~{annual_return:.1f}%/yr (planner assumption)")
    st.write(f"Starting invested (existing + lump sum): £{invest_start:,.0f}")
    st.write(f"Monthly contributions: £{invest_monthly_target:,.0f}")
    st.write(f"Horizon: {int(invest_years)} years")

    st.line_chart(invest_proj.set_index("year")[["balance"]])
    st.success(f"Estimated value after {int(invest_years)} years: £{invest_proj['balance'].iloc[-1]:,.0f}")

    st.caption("Deterministic projection (not a forecast). Real returns vary and losses are possible.")

    st.divider()
    st.subheader("Exports")

    summary = {
        "nation": nation,
        "household_takehome_annual": household_takehome_annual,
        "avg_monthly_income": monthly_income_display,
        "monthly_outgoings": total_outgoings_monthly,
        "avg_monthly_surplus": monthly_income_display - total_outgoings_monthly,
        "childcare_net_annual": childcare_net_annual,
        "child_benefit_annual": cb_annual,
        "hicbc_annual": hicbc_annual,
        "emergency_target": emergency_target,
        "emergency_current": emergency_current,
        "emergency_monthly_needed": emergency_monthly_needed,
        "invest_start": invest_start,
        "invest_monthly": invest_monthly_target,
        "invest_years": int(invest_years),
        "invest_end_estimate": float(invest_proj["balance"].iloc[-1]),
    }
    summary_df = pd.DataFrame([summary])

    st.download_button(
        "Download summary (CSV)",
        data=summary_df.to_csv(index=False).encode("utf-8"),
        file_name="uk_wealth_planner_summary.csv",
        mime="text/csv",
    )

    st.download_button(
        "Download cashflow (CSV)",
        data=timeline_df.to_csv(index=False).encode("utf-8"),
        file_name="uk_wealth_planner_cashflow.csv",
        mime="text/csv",
    )

    pdf_lines = [
        f"Nation: {nation}",
        f"Household take-home (annual): £{household_takehome_annual:,.0f}",
        f"Avg monthly income: £{monthly_income_display:,.0f}",
        f"Monthly outgoings (incl goals): £{total_outgoings_monthly:,.0f}",
        f"Avg monthly surplus: £{(monthly_income_display - total_outgoings_monthly):,.0f}",
        f"Childcare net (annual): £{childcare_net_annual:,.0f}",
        f"Child Benefit (annual): £{cb_annual:,.0f}",
        f"HICBC est (annual): £{hicbc_annual:,.0f}",
        f"Emergency target: £{emergency_target:,.0f} | Current: £{emergency_current:,.0f} | Monthly needed: £{emergency_monthly_needed:,.0f}",
        f"Investing: start £{invest_start:,.0f}, +£{invest_monthly_target:,.0f}/mo, {int(invest_years)} yrs, return ~{annual_return:.1f}% => £{invest_proj['balance'].iloc[-1]:,.0f}",
    ]
    pdf_bytes = make_pdf_report(pdf_lines)

    st.download_button(
        "Download report (PDF)",
        data=pdf_bytes,
        file_name="uk_wealth_planner_report.pdf",
        mime="application/pdf",
    )
