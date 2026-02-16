import pandas as pd
import streamlit as st

from core.models import Debt, ChildCareChild
from core.pensions import annual_bonus_to_annual, estimate_annual_allowance, cap_employee_contrib
from core.childcare import estimate_childcare
from core.cashflow import (
    debts_monthly_total,
    essentials_monthly,
    emergency_plan,
    cashflow_12m,
    amortisation_schedule,
)
from core.investing import RISK_PROFILES, ETF_SUGGESTIONS, projection_yearly
from core.tax import estimate_tax


st.set_page_config(page_title="UK Wealth Planner", layout="wide")
st.title("UK Wealth Planner — Modular")
st.caption("Planner tool only (not financial/tax advice). Uses simplified assumptions.")


# ---------------------------
# Sidebar inputs
# ---------------------------
with st.sidebar:
    st.header("Household")

    nation = st.selectbox(
        "Nation (for childcare logic)",
        ["England/Wales/Northern Ireland", "Scotland"],
        help="Used for childcare scheme modelling (England vs Scotland differs).",
    )
    is_scotland = (nation == "Scotland")

    has_partner = st.checkbox("Include partner", value=True, help="Adds partner salary/bonus/pension to household inputs.")
    children_count = st.number_input("Number of children", 0, 10, 1, help="Used for childcare modelling inputs.")

    mpaa_triggered = st.checkbox(
        "MPAA triggered? (flexibly accessed DC pension)",
        value=False,
        help="If yes, DC pension annual allowance can be reduced (simplified).",
    )

    st.divider()
    st.header("Income — You")

    you_salary = st.number_input("Salary (£/year)", min_value=0.0, value=65000.0, step=1000.0, help="Gross annual salary.")
    you_bonus_mode = st.radio(
        "Bonus frequency",
        ["None", "Annual", "Quarterly"],
        horizontal=True,
        help="Bonus is lumpy in cashflow (annual in month 12, quarterly in months 3/6/9/12).",
    )
    if you_bonus_mode == "Annual":
        you_bonus_amount = st.number_input("Annual bonus (£)", min_value=0.0, value=5000.0, step=500.0)
    elif you_bonus_mode == "Quarterly":
        you_bonus_amount = st.number_input("Bonus per quarter (£)", min_value=0.0, value=1500.0, step=250.0)
    else:
        you_bonus_amount = 0.0

    st.subheader("Pension — You")
    you_emp_pension = st.number_input(
        "Employee pension (£/year)",
        min_value=0.0,
        value=6000.0,
        step=250.0,
        help="Counts towards annual allowance (simplified).",
    )
    you_employer_pension = st.number_input(
        "Employer pension (£/year)",
        min_value=0.0,
        value=3000.0,
        step=250.0,
        help="Counts towards annual allowance (simplified).",
    )

    partner_salary = 0.0
    partner_bonus_mode = "None"
    partner_bonus_amount = 0.0
    partner_emp_pension = 0.0
    partner_employer_pension = 0.0

    if has_partner:
        st.divider()
        st.header("Income — Partner")

        partner_salary = st.number_input("Partner salary (£/year)", min_value=0.0, value=42000.0, step=1000.0)
        partner_bonus_mode = st.radio("Partner bonus frequency", ["None", "Annual", "Quarterly"], horizontal=True, key="p_bonus_mode")
        if partner_bonus_mode == "Annual":
            partner_bonus_amount = st.number_input("Partner annual bonus (£)", min_value=0.0, value=0.0, step=500.0, key="p_bonus_a")
        elif partner_bonus_mode == "Quarterly":
            partner_bonus_amount = st.number_input("Partner bonus per quarter (£)", min_value=0.0, value=0.0, step=250.0, key="p_bonus_q")
        else:
            partner_bonus_amount = 0.0

        st.subheader("Pension — Partner")
        partner_emp_pension = st.number_input("Partner employee pension (£/year)", min_value=0.0, value=3000.0, step=250.0, key="p_emp_pen")
        partner_employer_pension = st.number_input("Partner employer pension (£/year)", min_value=0.0, value=2000.0, step=250.0, key="p_empr_pen")

    st.divider()
    st.header("Savings & other income")

    savings_balance = st.number_input(
        "Total savings (£)",
        min_value=0.0,
        value=10000.0,
        step=500.0,
        help="Used to estimate annual interest income.",
    )
    savings_interest_pct = st.number_input(
        "Average savings interest rate (%)",
        min_value=0.0,
        value=4.0,
        step=0.1,
        help="Estimate across savings accounts.",
    )
    dividends_annual = st.number_input("Dividends (£/year)", min_value=0.0, value=0.0, step=100.0)
    rental_net_annual = st.number_input("Rental net income (£/year)", min_value=0.0, value=0.0, step=250.0)

    st.divider()
    st.header("Mortgage")

    mortgage_payment = st.number_input("Mortgage payment (£/mo)", min_value=0.0, value=1600.0, step=50.0)
    mortgage_balance = st.number_input("Mortgage balance (£)", min_value=0.0, value=300000.0, step=5000.0)
    mortgage_rate = st.number_input("Mortgage interest rate (%)", min_value=0.0, value=4.5, step=0.1)
    mortgage_term_years = st.number_input("Mortgage term remaining (years)", min_value=1, value=25, step=1)

    st.divider()
    st.header("Other debts (multiple)")
    st.caption("Add rows. End Date optional (YYYY-MM-DD).")

    # Persist editor contents across reruns
    if "debts_df" not in st.session_state:
        st.session_state["debts_df"] = pd.DataFrame(
            [{"name": "Car finance", "balance": 8000.0, "apr_pct": 9.9, "monthly_payment": 250.0, "end_date": ""}]
        )

    debts_df = st.data_editor(st.session_state["debts_df"], num_rows="dynamic", use_container_width=True)
    st.session_state["debts_df"] = debts_df

    st.divider()
    st.header("Monthly spend")

    food = st.number_input("Food (£/mo)", min_value=0.0, value=650.0, step=25.0, help="Often one of the biggest controllable categories.")
    utilities = st.number_input("Utilities (£/mo)", min_value=0.0, value=320.0, step=25.0)
    transport = st.number_input("Transport (£/mo)", min_value=0.0, value=350.0, step=25.0)
    leisure = st.number_input("Leisure (£/mo)", min_value=0.0, value=450.0, step=25.0)
    subscriptions = st.number_input("Subscriptions (£/mo)", min_value=0.0, value=55.0, step=5.0)
    shopping = st.number_input("Shopping/misc (£/mo)", min_value=0.0, value=250.0, step=25.0)

    st.divider()
    st.header("Holidays")

    holiday_cost_each = st.number_input("Cost per holiday (£)", min_value=0.0, value=2500.0, step=100.0)
    holidays_per_year = st.number_input("Holidays per year", min_value=0, max_value=6, value=2, step=1)

    st.divider()
    st.header("Emergency fund (fixed)")

    emergency_target_months = st.slider(
        "Target months of essentials",
        0, 12, 3,
        help="Default is 3 months, but change to match your preference.",
    )
    emergency_current_balance = st.number_input("Current emergency fund (£)", min_value=0.0, value=0.0, step=250.0)
    emergency_build_months = st.slider("Build remaining gap over (months)", 1, 36, 12)

    st.divider()
    st.header("Childcare / nursery")
    st.caption("Estimate only. Removes document upload for privacy/security.")

    childcare_children = []
    if children_count > 0:
        for i in range(int(children_count)):
            st.markdown(f"**Child {i+1}**")
            age = st.number_input(f"Age (years) — child {i+1}", 0.0, 18.0, 3.0, 0.25, key=f"age_{i}")
            days = st.number_input(f"Days/week — child {i+1}", 0.0, 7.0, 3.0, 0.5, key=f"days_{i}")
            hrs = st.number_input(f"Hours/day — child {i+1}", 0.0, 12.0, 10.0, 0.5, key=f"hrs_{i}")
            cost = st.number_input(f"Cost/day (£) — child {i+1}", 0.0, 500.0, 70.0, 5.0, key=f"cost_{i}")
            childcare_children.append(ChildCareChild(age_years=age, days_per_week=days, hours_per_day=hrs, cost_per_day=cost))

    child_benefit_received_annual = st.number_input(
        "Child Benefit received (£/year)",
        min_value=0.0,
        value=0.0,
        step=100.0,
        help="Optional. Used to estimate the High Income Child Benefit Charge (HICBC).",
    )

    st.divider()
    st.header("Scenarios")

    rate_rise_shock_pts = st.slider("Interest rate shock (+% points)", 0.0, 5.0, 1.0, 0.25)
    income_drop_pct = st.slider("Income reduction (%)", 0, 50, 0, 5)
    one_off_cost_month3 = st.number_input("One-off cost in month 3 (£)", min_value=0.0, value=0.0, step=100.0)

    st.divider()
    st.header("Investing")

    risk_profile = st.select_slider("Risk profile", options=list(RISK_PROFILES.keys()), value="Balanced")
    invest_monthly = st.number_input("Monthly investing (£/mo)", min_value=0.0, value=500.0, step=25.0)
    invest_existing = st.number_input("Already invested (£)", min_value=0.0, value=10000.0, step=500.0)
    invest_lump_sum = st.number_input("Lump sum to invest now (£)", min_value=0.0, value=0.0, step=500.0)
    invest_years = st.number_input("Years to invest", min_value=1, value=10, step=1)


# ---------------------------
# Parse debts
# ---------------------------
debts = []
if isinstance(debts_df, pd.DataFrame) and not debts_df.empty:
    for _, r in debts_df.iterrows():
        debts.append(
            Debt(
                name=str(r.get("name", "Debt")).strip() or "Debt",
                balance=float(r.get("balance", 0.0) or 0.0),
                apr_pct=float(r.get("apr_pct", 0.0) or 0.0),
                monthly_payment=float(r.get("monthly_payment", 0.0) or 0.0),
                end_date=str(r.get("end_date", "")).strip() or None,
            )
        )

debt_monthly_total = debts_monthly_total(debts)

# ---------------------------
# Bonus + pension cap logic
# ---------------------------
you_bonus_annual = annual_bonus_to_annual(you_bonus_mode, you_bonus_amount)
partner_bonus_annual = annual_bonus_to_annual(partner_bonus_mode, partner_bonus_amount)

you_gross = you_salary + you_bonus_annual
partner_gross = partner_salary + partner_bonus_annual

you_allowance = estimate_annual_allowance(you_gross, you_emp_pension, you_employer_pension, mpaa_triggered)
you_emp_pension_used = cap_employee_contrib(you_allowance, you_employer_pension, you_emp_pension)

partner_allowance = 0.0
partner_emp_pension_used = 0.0
if has_partner:
    partner_allowance = estimate_annual_allowance(partner_gross, partner_emp_pension, partner_employer_pension, mpaa_triggered)
    partner_emp_pension_used = cap_employee_contrib(partner_allowance, partner_employer_pension, partner_emp_pension)

# ---------------------------
# Childcare estimate
# Proxy adjusted net = gross - employee pension used
# ---------------------------
you_adj_proxy = max(0.0, you_gross - you_emp_pension_used)
partner_adj_proxy = max(0.0, partner_gross - partner_emp_pension_used) if has_partner else 0.0
highest_adj_proxy = max(you_adj_proxy, partner_adj_proxy)

childcare = estimate_childcare(
    nation=("Scotland" if is_scotland else "England/Wales/Northern Ireland"),
    highest_parent_adj_net=highest_adj_proxy,
    children=childcare_children,
)

# ---------------------------
# Savings interest + other income
# ---------------------------
savings_interest_annual = savings_balance * (savings_interest_pct / 100.0)
other_income_annual = savings_interest_annual + dividends_annual + rental_net_annual

# ---------------------------
# Tax engine (annual) -> net monthly income
# ---------------------------
tax_nation = "Scotland" if is_scotland else "England/Wales/Northern Ireland"
household_employment_income = you_gross + (partner_gross if has_partner else 0.0)
household_employee_pension = you_emp_pension_used + (partner_emp_pension_used if has_partner else 0.0)

tax = estimate_tax(
    nation=tax_nation,
    employment_income=household_employment_income,
    pension_employee=household_employee_pension,
    savings_interest=savings_interest_annual,
    dividends=dividends_annual,
    rental_income=rental_net_annual,
    child_benefit_received=child_benefit_received_annual,
    highest_parent_adjusted_net_for_hicbc=highest_adj_proxy,
)

monthly_income_base = max(0.0, tax.net_income) / 12.0

# ---------------------------
# Budget / emergency / goals
# ---------------------------
holiday_sinking_monthly = (holiday_cost_each * holidays_per_year) / 12.0

essentials = essentials_monthly(
    mortgage=mortgage_payment,
    utilities=utilities,
    food=food,
    transport=transport,
    debts_payment_total=debt_monthly_total,
    childcare=childcare["net_monthly"],
)

em = emergency_plan(
    essentials=essentials,
    target_months=emergency_target_months,
    current_balance=emergency_current_balance,
    build_months=emergency_build_months,
)

expenses_monthly = (
    mortgage_payment
    + debt_monthly_total
    + childcare["net_monthly"]
    + food
    + utilities
    + transport
    + leisure
    + subscriptions
    + shopping
)

goals_monthly = holiday_sinking_monthly + em["per_month"] + invest_monthly

# For cashflow timing: pass the lumpy amounts:
you_cash_bonus_amt = you_bonus_amount if you_bonus_mode == "Quarterly" else you_bonus_annual
partner_cash_bonus_amt = partner_bonus_amount if partner_bonus_mode == "Quarterly" else partner_bonus_annual

timeline = cashflow_12m(
    monthly_base_income=monthly_income_base,
    you_bonus_mode=you_bonus_mode,
    you_bonus_amount=you_cash_bonus_amt,
    partner_bonus_mode=partner_bonus_mode,
    partner_bonus_amount=partner_cash_bonus_amt,
    total_outgoings_monthly=expenses_monthly + goals_monthly,
    income_drop_pct=income_drop_pct,
    one_off_cost_month3=one_off_cost_month3,
)

# Mortgage amortisation (rate shock applied)
mort_sched = amortisation_schedule(
    balance=mortgage_balance,
    apr_pct=mortgage_rate + rate_rise_shock_pts,
    monthly_payment=mortgage_payment,
    months=mortgage_term_years * 12,
)

# Investment projection
risk = RISK_PROFILES[risk_profile]
annual_return = float(risk["annual_return"])
alloc = risk["alloc"]

invest_start = invest_existing + invest_lump_sum
proj = projection_yearly(invest_start, invest_monthly, invest_years, annual_return)

# ---------------------------
# Main page output
# ---------------------------
left, right = st.columns([1.25, 0.75], gap="large")

with left:
    st.subheader("Key outputs")

    avg_monthly_income = monthly_income_base
    avg_monthly_outgoings = expenses_monthly + goals_monthly
    avg_monthly_surplus = avg_monthly_income - avg_monthly_outgoings

    k1, k2, k3 = st.columns(3)
    k1.metric("Avg monthly net income", f"£{avg_monthly_income:,.0f}")
    k2.metric("Avg monthly outgoings", f"£{avg_monthly_outgoings:,.0f}")
    k3.metric("Avg monthly surplus", f"£{avg_monthly_surplus:,.0f}")

    st.divider()
    st.subheader("Tax estimate (annual)")

    t1, t2, t3 = st.columns(3)
    t1.metric("Gross income", f"£{tax.gross_income:,.0f}")
    t2.metric("Total tax", f"£{tax.total_tax:,.0f}")
    t3.metric("Net income", f"£{tax.net_income:,.0f}")

    with st.expander("Show tax breakdown"):
        st.write(f"Personal allowance: £{tax.personal_allowance:,.0f}")
        st.write(f"Income tax: £{tax.income_tax:,.0f}")
        st.write(f"Employee NI: £{tax.employee_ni:,.0f}")
        st.write(f"Dividend tax: £{tax.dividend_tax:,.0f}")
        st.write(f"Savings tax: £{tax.savings_tax:,.0f}")
        st.write(f"HICBC: £{tax.hicbc:,.0f}")

    st.divider()
    st.subheader("Emergency fund (corrected)")

    st.write(f"Essentials (monthly): **£{essentials:,.0f}**")
    st.write(f"Target ({emergency_target_months} months): **£{em['target']:,.0f}**")
    st.write(f"Current: **£{emergency_current_balance:,.0f}**")
    st.success(f"Gap: £{em['gap']:,.0f} → Save about **£{em['per_month']:,.0f}/mo** for {emergency_build_months} months")

    st.divider()
    st.subheader("Childcare estimate")

    st.write(f"Gross annual: £{childcare['gross_annual']:,.0f}")
    st.write(f"Funded value annual: £{childcare['funded_value_annual']:,.0f}")
    st.write(f"Tax-Free Childcare top-up annual: £{childcare['tfc_topup_annual']:,.0f}")
    st.success(f"Net childcare: £{childcare['net_annual']:,.0f}/yr  (≈ £{childcare['net_monthly']:,.0f}/mo)")

    if childcare.get("england_100k_blocked"):
        st.warning("England scheme note: a parent over £100k (proxy) blocks Tax-Free Childcare / working funded offer in this model.")

    st.divider()
    st.subheader("Cashflow timeline (12 months)")
    st.dataframe(timeline, use_container_width=True)
    st.line_chart(timeline.set_index("month")[["net", "cumulative"]])

    st.divider()
    st.subheader("Mortgage amortisation (shock applied)")
    st.caption(f"Mortgage APR used: {(mortgage_rate + rate_rise_shock_pts):.2f}%")
    st.dataframe(mort_sched.head(12), use_container_width=True)
    st.line_chart(mort_sched.set_index("month")[["balance"]].head(120))

with right:
    st.subheader("Pension cap (planner)")

    st.write(f"**You** allowance estimate: £{you_allowance:,.0f}/yr")
    st.write(f"Employer: £{you_employer_pension:,.0f} | Employee requested: £{you_emp_pension:,.0f}")
    st.success(f"Employee used (capped): **£{you_emp_pension_used:,.0f}/yr**")

    if has_partner:
        st.divider()
        st.write(f"**Partner** allowance estimate: £{partner_allowance:,.0f}/yr")
        st.write(f"Employer: £{partner_employer_pension:,.0f} | Employee requested: £{partner_emp_pension:,.0f}")
        st.success(f"Employee used (capped): **£{partner_emp_pension_used:,.0f}/yr**")

    st.divider()
    st.subheader("Investing allocation")

    alloc_df = pd.DataFrame({"Bucket": list(alloc.keys()), "Percent": list(alloc.values())})
    st.dataframe(alloc_df, use_container_width=True)

    st.markdown("**Example ETFs (illustrative):**")
    for bucket, pct in alloc.items():
        ex = ETF_SUGGESTIONS.get(bucket, [])
        if ex:
            st.write(f"- {bucket} ({pct}%): " + "; ".join(ex))

    st.divider()
    st.subheader("Investment projection")

    st.write(f"Assumed long-run return: ~{annual_return:.1f}%/yr (risk profile: {risk_profile})")
    st.write(f"Start invested (existing + lump sum): £{invest_start:,.0f}")
    st.write(f"Monthly contribution: £{invest_monthly:,.0f}")
    st.write(f"Horizon: {invest_years} years")

    st.line_chart(proj.set_index("year")[["balance"]])
    st.success(f"Estimated value after {invest_years} years: £{proj['balance'].iloc[-1]:,.0f}")
