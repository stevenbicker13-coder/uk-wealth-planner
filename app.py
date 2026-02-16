import streamlit as st
import pandas as pd
import numpy as np
import datetime as dt
import math

st.set_page_config(page_title="UK Wealth Planner", layout="wide")

st.title("UK Wealth Planner — Enhanced")
st.caption("Planner tool only (not financial/tax advice). Rules vary and can change.")

# =========================
# CONSTANTS
# =========================

ANNUAL_ALLOWANCE = 60000
HICBC_START = 50000
HICBC_END = 60000

RISK_RETURNS = {
    "Cautious": 4,
    "Balanced": 6,
    "Growth": 8
}

# =========================
# SIDEBAR INPUTS
# =========================

with st.sidebar:

    st.header("Location")
    nation = st.selectbox("Tax nation", ["England/Wales/Northern Ireland", "Scotland"])
    is_scotland = nation == "Scotland"

    st.divider()

    st.header("Income")

    salary = st.number_input("Salary (£/year)", 0.0, 1000000.0, 65000.0, step=1000.0)

    bonus_type = st.radio("Bonus type", ["None", "Annual", "Quarterly"])

    bonus_annual = 0
    if bonus_type == "Annual":
        bonus_annual = st.number_input("Annual bonus (£)", 0.0, 500000.0, 5000.0)
    elif bonus_type == "Quarterly":
        q_bonus = st.number_input("Bonus per quarter (£)", 0.0, 200000.0, 1500.0)
        bonus_annual = q_bonus * 4

    st.divider()

    st.header("Pension")

    emp_contribution = st.number_input("Employee pension (£/year)", 0.0, 200000.0, 6000.0)
    employer_contribution = st.number_input("Employer pension (£/year)", 0.0, 200000.0, 3000.0)

    # Enforce allowance
    max_employee = max(0, ANNUAL_ALLOWANCE - employer_contribution)
    if emp_contribution > max_employee:
        emp_contribution = max_employee
        st.warning("Employee contribution capped by £60k annual allowance (incl employer).")

    st.divider()

    st.header("Savings")

    savings_balance = st.number_input("Total savings (£)", 0.0, 10000000.0, 10000.0)
    savings_rate = st.number_input("Savings interest rate (%)", 0.0, 20.0, 4.0)

    dividends = st.number_input("Dividends (£/year)", 0.0, 1000000.0, 0.0)
    rental_income = st.number_input("Rental income net (£/year)", 0.0, 1000000.0, 0.0)

    st.divider()

    st.header("Mortgage")

    mortgage_balance = st.number_input("Mortgage balance (£)", 0.0, 2000000.0, 300000.0)
    mortgage_rate = st.number_input("Mortgage interest rate (%)", 0.0, 20.0, 4.5)
    mortgage_payment = st.number_input("Mortgage payment (£/mo)", 0.0, 10000.0, 1600.0)

    st.divider()

    st.header("Other Debts")
    st.caption("Add multiple debts. Use YYYY-MM-DD format for end date.")

    default_debts = pd.DataFrame(
        [{"Name": "Car Loan", "Balance": 8000.0, "APR %": 9.9, "Monthly Payment": 250.0, "End Date": ""}]
    )

    debts_df = st.data_editor(default_debts, num_rows="dynamic", use_container_width=True)

    st.divider()

    st.header("Spending (Monthly)")

    food = st.number_input("Food", 0.0, 5000.0, 650.0)
    utilities = st.number_input("Utilities", 0.0, 2000.0, 320.0)
    transport = st.number_input("Transport", 0.0, 2000.0, 350.0)
    leisure = st.number_input("Leisure", 0.0, 5000.0, 450.0)
    shopping = st.number_input("Shopping", 0.0, 5000.0, 250.0)

    st.divider()

    st.header("Emergency Fund")

    emergency_months = st.slider("Target months of essentials", 1, 12, 3)
    emergency_current = st.number_input("Current emergency savings (£)", 0.0, 500000.0, 0.0)
    emergency_build_time = st.slider("Build over months", 1, 36, 12)

    st.divider()

    st.header("Investing")

    risk_profile = st.selectbox("Risk profile", list(RISK_RETURNS.keys()))
    invest_monthly = st.number_input("Monthly investment (£)", 0.0, 20000.0, 500.0)
    invested_existing = st.number_input("Already invested (£)", 0.0, 5000000.0, 10000.0)
    invest_years = st.number_input("Years investing", 1, 50, 10)

# =========================
# CALCULATIONS
# =========================

annual_income = salary + bonus_annual
annual_interest = savings_balance * (savings_rate / 100)
other_income = dividends + rental_income + annual_interest

gross_total = annual_income + other_income

# Child Benefit simple model
child_benefit = 0
hicbc = 0

if gross_total > HICBC_START:
    reduction_pct = min(1, (gross_total - HICBC_START) / (HICBC_END - HICBC_START))
    child_benefit = 2000  # simple placeholder annual
    hicbc = child_benefit * reduction_pct

monthly_income_avg = (gross_total - emp_contribution - employer_contribution - hicbc) / 12

debt_monthly = debts_df["Monthly Payment"].sum()

expenses = mortgage_payment + debt_monthly + food + utilities + transport + leisure + shopping

# Emergency fund corrected
essentials = mortgage_payment + utilities + food + transport + debt_monthly
emergency_target = essentials * emergency_months
emergency_gap = max(0, emergency_target - emergency_current)
emergency_monthly_needed = emergency_gap / emergency_build_time

monthly_surplus = monthly_income_avg - expenses - emergency_monthly_needed - invest_monthly

# =========================
# INVESTMENT PROJECTION
# =========================

def compound_projection(start, monthly, years, annual_return):
    r = annual_return / 100 / 12
    months = years * 12
    balance = start
    balances = []
    for m in range(months):
        balance = balance * (1 + r) + monthly
        balances.append(balance)
    return balances

projection = compound_projection(invested_existing, invest_monthly, invest_years, RISK_RETURNS[risk_profile])

# =========================
# DISPLAY
# =========================

col1, col2 = st.columns(2)

with col1:
    st.subheader("Income Summary")
    st.metric("Annual gross income", f"£{gross_total:,.0f}")
    st.metric("Average monthly income", f"£{monthly_income_avg:,.0f}")
    st.metric("Monthly expenses", f"£{expenses:,.0f}")
    st.metric("Monthly surplus", f"£{monthly_surplus:,.0f}")

    st.divider()

    st.subheader("Emergency Fund")
    st.write(f"Target: £{emergency_target:,.0f}")
    st.write(f"Current: £{emergency_current:,.0f}")
    st.success(f"Save £{emergency_monthly_needed:,.0f}/mo for {emergency_build_time} months")

with col2:
    st.subheader("Investment Projection")
    st.write(f"Expected return: {RISK_RETURNS[risk_profile]}%")
    st.line_chart(pd.DataFrame(projection, columns=["Portfolio Value"]))
    st.success(f"Projected value after {invest_years} years: £{projection[-1]:,.0f}")
