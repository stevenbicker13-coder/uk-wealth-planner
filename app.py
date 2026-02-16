import io
import math
import datetime as dt
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import streamlit as st

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


# ---------------------------
# TAX CONFIG (simplified)
# ---------------------------

@dataclass(frozen=True)
class TaxBand:
    upper: Optional[float]  # None means no upper limit
    rate: float            # e.g. 0.20 for 20%

@dataclass(frozen=True)
class TaxConfig:
    personal_allowance: float = 12570.0
    pa_taper_start: float = 100000.0
    pa_taper_end: float = 125140.0

    # Employee Class 1 NI (annual, simplified)
    ni_primary_threshold: float = 12570.0
    ni_upper_earnings_limit: float = 50270.0
    ni_rate_main: float = 0.08
    ni_rate_additional: float = 0.02

    # rUK income tax
    ruk_bands: Tuple[TaxBand, ...] = (
        TaxBand(upper=50270.0, rate=0.20),
        TaxBand(upper=125140.0, rate=0.40),
        TaxBand(upper=None, rate=0.45),
    )

    # Scotland income tax (non-savings/non-dividend)
    scot_bands: Tuple[TaxBand, ...] = (
        TaxBand(upper=15397.0, rate=0.19),
        TaxBand(upper=27491.0, rate=0.20),
        TaxBand(upper=43662.0, rate=0.21),
        TaxBand(upper=75000.0, rate=0.42),
        TaxBand(upper=125140.0, rate=0.45),
        TaxBand(upper=None, rate=0.48),
    )

TAX = TaxConfig()


# ---------------------------
# HELPERS
# ---------------------------

def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

def personal_allowance_for_income(adjusted_net_income: float) -> float:
    if adjusted_net_income <= TAX.pa_taper_start:
        return TAX.personal_allowance
    if adjusted_net_income >= TAX.pa_taper_end:
        return 0.0
    reduction = (adjusted_net_income - TAX.pa_taper_start) / 2.0
    return max(0.0, TAX.personal_allowance - reduction)

def income_tax_due_employment_only(gross_income: float, pension_salary_sacrifice: float, is_scotland: bool) -> float:
    adjusted_net = max(0.0, gross_income - pension_salary_sacrifice)
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

def employee_ni_due(gross_income: float, pension_salary_sacrifice: float) -> float:
    ni_pay = max(0.0, gross_income - pension_salary_sacrifice)
    pt = TAX.ni_primary_threshold
    uel = TAX.ni_upper_earnings_limit

    if ni_pay <= pt:
        return 0.0

    main_slice = min(ni_pay, uel) - pt
    addl_slice = max(0.0, ni_pay - uel)

    return max(0.0, main_slice) * TAX.ni_rate_main + addl_slice * TAX.ni_rate_additional

def net_income_summary(
    salary: float,
    bonus: float,
    pension_emp_sacrifice: float,
    is_scotland: bool,
    extra_taxable_income: float = 0.0,
) -> Dict[str, float]:
    """
    Simplified:
    - Employment income = salary + bonus
    - Pension entered here is treated like salary sacrifice (reduces tax & NI base)
    - 'extra_taxable_income' is added AFTER tax/NI calc (very simplified).
      Use for rough rental/dividend/interest; proper modelling differs.
    """
    gross = max(0.0, salary + bonus)
    pension = clamp(pension_emp_sacrifice, 0.0, gross)

    tax = income_tax_due_employment_only(gross, pension, is_scotland=is_scotland)
    ni = employee_ni_due(gross, pension)

    take_home = max(0.0, gross - pension - tax - ni) + max(0.0, extra_taxable_income)
    return {"gross": gross, "pension": pension, "income_tax": tax, "ni": ni, "take_home": take_home}

def annual_from_percent(gross: float, pct: float) -> float:
    return gross * (pct / 100.0)

def amortisation_schedule(
    principal: float,
    annual_rate: float,
    term_years: int,
    monthly_payment: float,
    months: int = 360
) -> pd.DataFrame:
    """
    Basic amortisation for a loan/mortgage with a fixed monthly payment.
    """
    r = (annual_rate / 100.0) / 12.0
    bal = principal
    rows = []
    for m in range(1, months + 1):
        if bal <= 0:
            break
        interest = bal * r
        principal_paid = max(0.0, monthly_payment - interest)
        # Prevent negative balance overshoot
        principal_paid = min(principal_paid, bal)
        bal -= principal_paid
        rows.append({"month": m, "interest": interest, "principal_paid": principal_paid, "balance": bal})
    return pd.DataFrame(rows)

def compound_growth(
    lump_sum: float,
    monthly_contrib: float,
    years: int,
    annual_return_pct: float
) -> pd.DataFrame:
    r = (annual_return_pct / 100.0) / 12.0
    months = years * 12
    bal = lump_sum
    rows = []
    for m in range(1, months + 1):
        bal = bal * (1 + r) + monthly_contrib
        rows.append({"month": m, "balance": bal})
    df = pd.DataFrame(rows)
    df["year"] = (df["month"] / 12.0).apply(math.ceil)
    yearly = df.groupby("year")["balance"].last().reset_index()
    return yearly

def make_pdf_report(summary_lines: List[str]) -> bytes:
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    y = height - 50
    c.setFont("Helvetica-Bold", 16)
    c.drawString(40, y, "UK Wealth Planner — Report")
    y -= 30

    c.setFont("Helvetica", 10)
    c.drawString(40, y, f"Generated: {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    y -= 25

    c.setFont("Helvetica", 11)
    for line in summary_lines:
        if y < 60:
            c.showPage()
            y = height - 50
            c.setFont("Helvetica", 11)
        c.drawString(40, y, line[:110])
        y -= 16

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer.read()


# ---------------------------
# Spend benchmarks (placeholder)
# Replace with ONS / real dataset later.
# ---------------------------

BENCHMARKS = pd.DataFrame([
    # region, household_size, food_pct, leisure_pct, transport_pct, utilities_pct, subs_pct, shopping_pct
    ("London", 2, 0.11, 0.09, 0.10, 0.07, 0.03, 0.06),
    ("London", 4, 0.12, 0.07, 0.11, 0.08, 0.03, 0.05),
    ("South East", 2, 0.12, 0.08, 0.10, 0.08, 0.03, 0.06),
    ("South East", 4, 0.13, 0.07, 0.11, 0.08, 0.03, 0.05),
    ("Midlands", 2, 0.13, 0.08, 0.10, 0.09, 0.03, 0.06),
    ("Midlands", 4, 0.14, 0.07, 0.11, 0.09, 0.03, 0.05),
    ("North", 2, 0.14, 0.08, 0.10, 0.09, 0.03, 0.06),
    ("North", 4, 0.15, 0.07, 0.11, 0.09, 0.03, 0.05),
    ("Scotland", 2, 0.14, 0.08, 0.10, 0.09, 0.03, 0.06),
    ("Scotland", 4, 0.15, 0.07, 0.11, 0.09, 0.03, 0.05),
], columns=["region", "household_size", "food", "leisure", "transport", "utilities", "subscriptions", "shopping"])

def get_benchmarks(region: str, household_size: int) -> Dict[str, float]:
    household_size = 4 if household_size >= 4 else 2 if household_size >= 2 else 1
    # If size==1, reuse size==2 row as a rough approximation
    hs = 2 if household_size == 1 else household_size

    match = BENCHMARKS[(BENCHMARKS["region"] == region) & (BENCHMARKS["household_size"] == hs)]
    if match.empty:
        match = BENCHMARKS[(BENCHMARKS["region"] == "Midlands") & (BENCHMARKS["household_size"] == hs)]
    row = match.iloc[0].to_dict()
    return {k: float(row[k]) for k in ["food", "leisure", "transport", "utilities", "subscriptions", "shopping"]}


# ---------------------------
# Child Benefit + HICBC (simplified)
# ---------------------------

def annual_child_benefit(children: int) -> float:
    """
    Simplified rates may change over time.
    This is a calculator approximation; you should update the rates annually.
    """
    if children <= 0:
        return 0.0
    # Using approximate weekly rates (you should update):
    first = 25.60
    additional = 16.95
    weekly = first + max(0, children - 1) * additional
    return weekly * 52

def hicbc_charge(adjusted_net_income: float, child_benefit_annual: float) -> float:
    """
    High Income Child Benefit Charge (HICBC) simplified:
    - starts at 50,000
    - full at 60,000
    """
    if child_benefit_annual <= 0:
        return 0.0
    if adjusted_net_income <= 50000:
        return 0.0
    if adjusted_net_income >= 60000:
        return child_benefit_annual
    pct = (adjusted_net_income - 50000) / 10000  # 0..1
    return child_benefit_annual * pct


# ---------------------------
# Student loan (simplified)
# ---------------------------

STUDENT_LOAN_THRESHOLDS = {
    "None": 0.0,
    "Plan 1": 22015.0,
    "Plan 2": 27295.0,
    "Plan 4": 27660.0,
    "Plan 5": 25000.0,
    "Postgraduate": 21000.0,
}
STUDENT_LOAN_RATES = {
    "None": 0.00,
    "Plan 1": 0.09,
    "Plan 2": 0.09,
    "Plan 4": 0.09,
    "Plan 5": 0.09,
    "Postgraduate": 0.06,
}

def student_loan_annual(gross_income: float, plan: str) -> float:
    if plan not in STUDENT_LOAN_THRESHOLDS:
        plan = "None"
    thr = STUDENT_LOAN_THRESHOLDS[plan]
    rate = STUDENT_LOAN_RATES[plan]
    return max(0.0, gross_income - thr) * rate


# ---------------------------
# Investing model ETFs (examples)
# Use UCITS-style tickers as examples; you can replace with your preferred list.
# ---------------------------

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


# ---------------------------
# UI
# ---------------------------

st.set_page_config(page_title="UK Wealth Planner", layout="wide")
st.title("UK Wealth Planner — Enhanced Prototype")
st.caption("Planning tool only (not financial or tax advice). Tax rules vary and change; verify with HMRC / a professional.")

with st.sidebar:
    st.header("Household")
    nation = st.selectbox("Where do you pay income tax?", ["England/Wales/Northern Ireland", "Scotland"])
    is_scotland = nation == "Scotland"

    region = st.selectbox("Region (for benchmarks)", ["London", "South East", "Midlands", "North", "Scotland"])
    has_partner = st.checkbox("I have a partner (joint planning)", value=True)
    children = st.number_input("Number of children", min_value=0, max_value=10, value=0, step=1)
    household_size = 2 + int(children) + (1 if has_partner else 0)  # rough

    st.divider()
    st.header("Income (annual)")
    st.subheader("You")
    you_salary = st.number_input("Your salary (£/year)", min_value=0.0, value=65000.0, step=1000.0)
    you_bonus = st.number_input("Your bonus (£/year)", min_value=0.0, value=5000.0, step=500.0)

    st.markdown("**Your pension contributions**")
    you_pension_mode = st.radio("Contribution type", ["% of (salary+bonus)", "£ per year"], horizontal=True, key="you_pension_mode")
    if you_pension_mode.startswith("%"):
        you_pension_pct = st.number_input("Your pension %", min_value=0.0, max_value=100.0, value=6.0, step=0.5)
        you_pension_emp = annual_from_percent(you_salary + you_bonus, you_pension_pct)
    else:
        you_pension_emp = st.number_input("Your pension (£/year)", min_value=0.0, value=6000.0, step=250.0)

    st.markdown("**Employer pension**")
    you_employer_mode = st.radio("Employer contribution type", ["% of (salary+bonus)", "£ per year"], horizontal=True, key="you_employer_mode")
    if you_employer_mode.startswith("%"):
        you_employer_pct = st.number_input("Employer pension %", min_value=0.0, max_value=100.0, value=3.0, step=0.5)
        you_pension_employer = annual_from_percent(you_salary + you_bonus, you_employer_pct)
    else:
        you_pension_employer = st.number_input("Employer pension (£/year)", min_value=0.0, value=3000.0, step=250.0)

    you_student_plan = st.selectbox("Student loan plan (you)", list(STUDENT_LOAN_THRESHOLDS.keys()), index=0)

    partner_salary = partner_bonus = partner_pension_emp = partner_pension_employer = 0.0
    partner_student_plan = "None"
    if has_partner:
        st.subheader("Partner")
        partner_salary = st.number_input("Partner salary (£/year)", min_value=0.0, value=42000.0, step=1000.0)
        partner_bonus = st.number_input("Partner bonus (£/year)", min_value=0.0, value=0.0, step=500.0)

        st.markdown("**Partner pension contributions**")
        partner_pension_mode = st.radio("Partner contribution type", ["% of (salary+bonus)", "£ per year"], horizontal=True, key="p_pension_mode")
        if partner_pension_mode.startswith("%"):
            p_pct = st.number_input("Partner pension %", min_value=0.0, max_value=100.0, value=5.0, step=0.5)
            partner_pension_emp = annual_from_percent(partner_salary + partner_bonus, p_pct)
        else:
            partner_pension_emp = st.number_input("Partner pension (£/year)", min_value=0.0, value=3000.0, step=250.0)

        st.markdown("**Partner employer pension**")
        partner_employer_mode = st.radio("Partner employer type", ["% of (salary+bonus)", "£ per year"], horizontal=True, key="p_emp_mode")
        if partner_employer_mode.startswith("%"):
            p_emp_pct = st.number_input("Partner employer %", min_value=0.0, max_value=100.0, value=3.0, step=0.5)
            partner_pension_employer = annual_from_percent(partner_salary + partner_bonus, p_emp_pct)
        else:
            partner_pension_employer = st.number_input("Partner employer (£/year)", min_value=0.0, value=2000.0, step=250.0)

        partner_student_plan = st.selectbox("Student loan plan (partner)", list(STUDENT_LOAN_THRESHOLDS.keys()), index=0)

    st.divider()
    st.header("Other income (annual, rough)")
    dividends = st.number_input("Dividends (£/year)", min_value=0.0, value=0.0, step=100.0)
    interest = st.number_input("Savings interest (£/year)", min_value=0.0, value=0.0, step=50.0)
    rental = st.number_input("Rental net income (£/year)", min_value=0.0, value=0.0, step=250.0)

    st.divider()
    st.header("Debt / Housing (monthly)")
    mortgage_payment = st.number_input("Mortgage payment (£/mo)", min_value=0.0, value=1600.0, step=50.0)
    mortgage_balance = st.number_input("Mortgage balance (£)", min_value=0.0, value=300000.0, step=5000.0)
    mortgage_rate = st.number_input("Mortgage interest rate (%)", min_value=0.0, value=4.5, step=0.1)
    mortgage_term_years = st.number_input("Mortgage term remaining (years)", min_value=1, value=25, step=1)

    other_debt_payment = st.number_input("Other debt payments (£/mo)", min_value=0.0, value=300.0, step=25.0)
    other_debt_balance = st.number_input("Other debt balance (£)", min_value=0.0, value=8000.0, step=500.0)
    other_debt_rate = st.number_input("Other debt interest rate (%)", min_value=0.0, value=19.9, step=0.1)

    st.divider()
    st.header("Spending (monthly)")
    food = st.number_input("Food & groceries (£/mo)", min_value=0.0, value=650.0, step=25.0)
    leisure = st.number_input("Leisure / eating out / hobbies (£/mo)", min_value=0.0, value=450.0, step=25.0)
    transport = st.number_input("Transport (£/mo)", min_value=0.0, value=350.0, step=25.0)
    utilities = st.number_input("Utilities (£/mo)", min_value=0.0, value=320.0, step=25.0)
    subscriptions = st.number_input("Subscriptions (£/mo)", min_value=0.0, value=55.0, step=5.0)
    shopping = st.number_input("Shopping / misc (£/mo)", min_value=0.0, value=250.0, step=25.0)

    st.divider()
    st.header("Savings goals")
    holiday_cost_each = st.number_input("Cost per family holiday (£)", min_value=0.0, value=2500.0, step=100.0)
    holidays_per_year = st.number_input("Holidays per year", min_value=0, max_value=6, value=2, step=1)
    emergency_months = st.slider("Emergency fund target (months of essentials)", 0, 12, 3)
    invest_monthly_target = st.number_input("Investing target (£/mo)", min_value=0.0, value=500.0, step=25.0)

    st.divider()
    st.header("Scenarios")
    rate_rise = st.slider("Rate rise shock (+% points)", 0.0, 5.0, 1.0, 0.25)
    income_drop_pct = st.slider("Income drop (%)", 0, 50, 0, 5)
    one_off_cost = st.number_input("One-off cost in month 3 (£)", min_value=0.0, value=0.0, step=100.0)

    st.divider()
    st.header("Investing")
    risk_profile = st.select_slider("Risk profile", options=list(RISK_PROFILES.keys()), value="Balanced")

    st.divider()
    st.header("Document upload (optional)")
    st.warning(
        "Uploading P60s/bank statements can expose sensitive personal data. "
        "For a public app, avoid uploading real statements unless you have privacy controls in place."
    )
    p60_file = st.file_uploader("Upload P60 (optional)", type=["pdf", "jpg", "png"])
    bank_file = st.file_uploader("Upload bank statement (optional)", type=["pdf", "csv"])


# --- Calculations ---

you_gross = you_salary + you_bonus
partner_gross = partner_salary + partner_bonus

# Student loan (annual)
you_student_annual = student_loan_annual(you_gross, you_student_plan)
partner_student_annual = student_loan_annual(partner_gross, partner_student_plan) if has_partner else 0.0

# Child benefit + HICBC (estimate based on highest adjusted net income)
cb = annual_child_benefit(int(children))
# Approx adjusted net income = employment gross - pension (salary sacrifice-ish)
you_adj = max(0.0, you_gross - you_pension_emp)
partner_adj = max(0.0, partner_gross - partner_pension_emp) if has_partner else 0.0
highest_adj = max(you_adj, partner_adj)
hicbc = hicbc_charge(highest_adj, cb)

# Extra taxable income (very rough; real tax treatment differs)
extra_income_annual = dividends + interest + rental - hicbc  # subtract HICBC as a "charge" approximation

you_net = net_income_summary(
    salary=you_salary,
    bonus=you_bonus,
    pension_emp_sacrifice=you_pension_emp,
    is_scotland=is_scotland,
    extra_taxable_income=0.0
)
partner_net = None
if has_partner:
    partner_net = net_income_summary(
        salary=partner_salary,
        bonus=partner_bonus,
        pension_emp_sacrifice=partner_pension_emp,
        is_scotland=is_scotland,
        extra_taxable_income=0.0
    )

household_take_home_annual = you_net["take_home"] + (partner_net["take_home"] if partner_net else 0.0)

# subtract student loans & add other income minus HICBC (rough)
household_take_home_annual = max(0.0, household_take_home_annual - you_student_annual - partner_student_annual + extra_income_annual)
household_take_home_monthly = household_take_home_annual / 12.0

holiday_sinking_monthly = (holiday_cost_each * holidays_per_year) / 12.0
expenses = {
    "mortgage": mortgage_payment,
    "other_debt": other_debt_payment,
    "food": food,
    "leisure": leisure,
    "transport": transport,
    "utilities": utilities,
    "subscriptions": subscriptions,
    "shopping": shopping,
}
goals = {"holidays": holiday_sinking_monthly, "investing": invest_monthly_target}

total_outgoings = sum(expenses.values()) + sum(goals.values())
surplus = household_take_home_monthly - total_outgoings

# Essentials estimate
essentials = mortgage_payment + utilities + food + transport + other_debt_payment
emergency_target = essentials * emergency_months

# Benchmarks for suggestions
bench = get_benchmarks(region, household_size)
bench_suggestions = []
if household_take_home_monthly > 0:
    for cat in ["food", "leisure", "transport", "utilities", "subscriptions", "shopping"]:
        current = expenses.get(cat, 0.0)
        target = household_take_home_monthly * bench[cat]
        if current > target * 1.15:
            bench_suggestions.append(
                f"{cat.title()} is high (£{current:,.0f}/mo). Benchmark ~£{target:,.0f}/mo → potential trim ~£{(current-target):,.0f}/mo."
            )

# Scenario: rate rise effect on debt interest (simple approximation)
mort_rate_scenario = mortgage_rate + rate_rise
other_rate_scenario = other_debt_rate + rate_rise

mort_sched = amortisation_schedule(
    principal=mortgage_balance,
    annual_rate=mort_rate_scenario,
    term_years=int(mortgage_term_years),
    monthly_payment=mortgage_payment,
    months=360
)
other_sched = amortisation_schedule(
    principal=other_debt_balance,
    annual_rate=other_rate_scenario,
    term_years=10,
    monthly_payment=other_debt_payment,
    months=120
)

# Cashflow timeline (12 months)
months = list(range(1, 13))
income_base = household_take_home_monthly
income_scenario = income_base * (1 - income_drop_pct/100.0)

timeline = []
for m in months:
    inc = income_scenario
    out = total_outgoings
    if m == 3 and one_off_cost > 0:
        out += one_off_cost
    timeline.append({"month": m, "income": inc, "outgoings": out, "net": inc - out})
timeline_df = pd.DataFrame(timeline)
timeline_df["cumulative"] = timeline_df["net"].cumsum()

# Investing
risk = RISK_PROFILES[risk_profile]
alloc = risk["alloc"]
annual_return = risk["annual_return"]


# ---------------------------
# DISPLAY
# ---------------------------

left, right = st.columns([1.2, 0.8], gap="large")

with left:
    st.subheader("Income & Tax (estimate)")
    c1, c2 = st.columns(2)

    with c1:
        st.markdown("**You**")
        st.write(f"Employment gross: £{you_net['gross']:,.0f}/yr (salary+bonus)")
        st.write(f"Pension (employee): £{you_pension_emp:,.0f}/yr")
        st.write(f"Pension (employer): £{you_pension_employer:,.0f}/yr")
        st.write(f"Income Tax: £{you_net['income_tax']:,.0f}/yr")
        st.write(f"NI: £{you_net['ni']:,.0f}/yr")
        st.write(f"Student loan: £{you_student_annual:,.0f}/yr")
        st.success(f"Take-home (employment): £{you_net['take_home']:,.0f}/yr")

    with c2:
        if partner_net:
            st.markdown("**Partner**")
            st.write(f"Employment gross: £{partner_net['gross']:,.0f}/yr")
            st.write(f"Pension (employee): £{partner_pension_emp:,.0f}/yr")
            st.write(f"Pension (employer): £{partner_pension_employer:,.0f}/yr")
            st.write(f"Income Tax: £{partner_net['income_tax']:,.0f}/yr")
            st.write(f"NI: £{partner_net['ni']:,.0f}/yr")
            st.write(f"Student loan: £{partner_student_annual:,.0f}/yr")
            st.success(f"Take-home (employment): £{partner_net['take_home']:,.0f}/yr")
        else:
            st.info("Partner not included.")

    st.divider()
    st.subheader("Other income & benefits (rough)")
    st.write(f"Child Benefit (annual): £{cb:,.0f}")
    st.write(f"HICBC estimate (annual charge): £{hicbc:,.0f}")
    st.write(f"Dividends + interest + rental (annual, entered): £{(dividends+interest+rental):,.0f}")
    st.caption("Note: Proper tax treatment for dividends/interest/rental differs. This is a rough household cashflow estimate.")

    st.divider()
    st.subheader("Monthly plan (current inputs)")
    k1, k2, k3 = st.columns(3)
    k1.metric("Net income (monthly)", f"£{household_take_home_monthly:,.0f}")
    k2.metric("Total outgoings (monthly)", f"£{total_outgoings:,.0f}")
    k3.metric("Surplus / Deficit", f"£{surplus:,.0f}")

    st.caption("Outgoings include spending + holiday sinking fund + investing target.")

    st.divider()
    st.subheader("Cashflow timeline (12 months, scenarios applied)")
    st.dataframe(timeline_df, use_container_width=True)
    st.line_chart(timeline_df.set_index("month")[["net", "cumulative"]])

    st.divider()
    st.subheader("Debt amortisation (scenario rate applied)")
    st.caption(f"Mortgage rate used: {mort_rate_scenario:.2f}%. Other debt rate used: {other_rate_scenario:.2f}%.")

    if not mort_sched.empty:
        st.write("**Mortgage (first 12 months)**")
        st.dataframe(mort_sched.head(12), use_container_width=True)
        st.line_chart(mort_sched.set_index("month")[["balance"]].head(120))

    if not other_sched.empty:
        st.write("**Other debt (first 12 months)**")
        st.dataframe(other_sched.head(12), use_container_width=True)
        st.line_chart(other_sched.set_index("month")[["balance"]].head(60))


with right:
    st.subheader("Goals & guidance")

    st.markdown("**Holidays sinking fund**")
    st.write(f"To fund {holidays_per_year} holidays at £{holiday_cost_each:,.0f} each → save ~**£{holiday_sinking_monthly:,.0f}/mo**.")

    st.markdown("**Emergency fund**")
    st.write(f"Essentials estimate: £{essentials:,.0f}/mo → target ({emergency_months} months): **£{emergency_target:,.0f}**")

    st.divider()
    st.subheader("Benchmark-based trim suggestions")
    if bench_suggestions:
        for s in bench_suggestions:
            st.write("• " + s)
    else:
        st.write("Spending looks broadly within the benchmark ranges for your selected region/household size.")

    st.divider()
    st.subheader("Investing: allocation + ETF ideas")
    st.write(f"Risk profile: **{risk_profile}** (assumed long-run return ~{annual_return:.1f}%/yr for calculator)")

    alloc_df = pd.DataFrame({"Bucket": list(alloc.keys()), "Percent": list(alloc.values())})
    st.dataframe(alloc_df, use_container_width=True)

    # Pie chart
    st.pyplot(alloc_df.set_index("Bucket").plot.pie(y="Percent", legend=False, ylabel="").figure)

    st.markdown("**Example ETFs (swap for your preferred UCITS list):**")
    for bucket, pct in alloc.items():
        etfs = ETF_SUGGESTIONS.get(bucket, [])
        if etfs:
            st.write(f"- {bucket} ({pct}%): " + "; ".join(etfs))

    st.divider()
    st.subheader("Investment calculator")
    lump = st.number_input("Lump sum (£)", min_value=0.0, value=10000.0, step=500.0)
    monthly = st.number_input("Monthly contribution (£)", min_value=0.0, value=500.0, step=25.0)
    years = st.number_input("Years to invest", min_value=1, value=10, step=1)

    growth = compound_growth(lump, monthly, int(years), annual_return)
    st.line_chart(growth.set_index("year")[["balance"]])
    st.success(f"Estimated value after {years} years: £{growth['balance'].iloc[-1]:,.0f}")

    st.caption("This is a deterministic projection, not a forecast. Real returns vary and losses are possible.")

    st.divider()
    st.subheader("Exports")
    # CSV exports
    export_summary = {
        "net_income_monthly": household_take_home_monthly,
        "outgoings_monthly": total_outgoings,
        "surplus_monthly": surplus,
        "holiday_sinking_monthly": holiday_sinking_monthly,
        "emergency_target": emergency_target,
        "child_benefit_annual": cb,
        "hicbc_annual": hicbc,
        "student_loan_annual_total": you_student_annual + partner_student_annual,
    }
    summary_df = pd.DataFrame([export_summary])

    st.download_button(
        "Download summary (CSV)",
        data=summary_df.to_csv(index=False).encode("utf-8"),
        file_name="uk_wealth_planner_summary.csv",
        mime="text/csv",
    )

    st.download_button(
        "Download cashflow timeline (CSV)",
        data=timeline_df.to_csv(index=False).encode("utf-8"),
        file_name="uk_wealth_planner_cashflow.csv",
        mime="text/csv",
    )

    # PDF export
    pdf_lines = [
        f"Net income (monthly): £{household_take_home_monthly:,.0f}",
        f"Total outgoings (monthly): £{total_outgoings:,.0f}",
        f"Surplus/Deficit (monthly): £{surplus:,.0f}",
        f"Holidays sinking fund (monthly): £{holiday_sinking_monthly:,.0f}",
        f"Emergency fund target: £{emergency_target:,.0f}",
        f"Child Benefit (annual): £{cb:,.0f}",
        f"HICBC estimate (annual): £{hicbc:,.0f}",
        f"Student loan (annual total): £{(you_student_annual + partner_student_annual):,.0f}",
        f"Risk profile: {risk_profile} (assumed return {annual_return:.1f}%/yr)",
        f"Allocation: {', '.join([f'{k} {v}%' for k,v in alloc.items()])}",
    ]
    pdf_bytes = make_pdf_report(pdf_lines)

    st.download_button(
        "Download report (PDF)",
        data=pdf_bytes,
        file_name="uk_wealth_planner_report.pdf",
        mime="application/pdf",
    )
