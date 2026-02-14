import math
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional

import streamlit as st


# ---------------------------
# UK TAX CONFIG (2025/26-style)
# Sources: GOV.UK Income Tax bands; GOV.UK NI rates/thresholds; Scotland bands (GOV.UK / gov.scot).
# You SHOULD keep these values in a config file and update annually.
# ---------------------------

@dataclass(frozen=True)
class TaxBand:
    upper: Optional[float]  # None means no upper limit
    rate: float            # e.g., 0.20 for 20%

@dataclass(frozen=True)
class TaxConfig:
    personal_allowance: float = 12570.0
    pa_taper_start: float = 100000.0
    pa_taper_end: float = 125140.0

    # Employee Class 1 NI (annualised from weekly/monthly thresholds)
    # Using annual equivalents of PT=12,570 and UEL=50,270
    ni_primary_threshold: float = 12570.0
    ni_upper_earnings_limit: float = 50270.0
    ni_rate_main: float = 0.08
    ni_rate_additional: float = 0.02

    # rUK income tax (England/Wales/NI)
    ruk_bands: Tuple[TaxBand, ...] = (
        TaxBand(upper=50270.0, rate=0.20),
        TaxBand(upper=125140.0, rate=0.40),
        TaxBand(upper=None, rate=0.45),
    )

    # Scotland income tax (2025/26 bands; “non-savings, non-dividend”)
    scot_bands: Tuple[TaxBand, ...] = (
        TaxBand(upper=15397.0, rate=0.19),
        TaxBand(upper=27491.0, rate=0.20),
        TaxBand(upper=43662.0, rate=0.21),
        TaxBand(upper=75000.0, rate=0.42),
        TaxBand(upper=125140.0, rate=0.45),
        TaxBand(upper=None, rate=0.48),
    )

    # Dividend allowance (planning hint; not fully modeled here)
    dividend_allowance: float = 500.0

    # CGT annual exempt amount (planning hint; not fully modeled here)
    cgt_aea: float = 3000.0

    # ISA allowance (planning hint)
    isa_allowance: float = 20000.0

    # Marriage allowance transfer amount (planning hint)
    marriage_allowance_transfer: float = 1260.0


TAX = TaxConfig()


# ---------------------------
# TAX CALC HELPERS
# ---------------------------

def personal_allowance_for_income(adjusted_net_income: float, tax: TaxConfig = TAX) -> float:
    """
    Standard personal allowance tapered down by £1 for each £2 over taper start.
    """
    if adjusted_net_income <= tax.pa_taper_start:
        return tax.personal_allowance
    if adjusted_net_income >= tax.pa_taper_end:
        return 0.0
    reduction = (adjusted_net_income - tax.pa_taper_start) / 2.0
    return max(0.0, tax.personal_allowance - reduction)


def income_tax_due(
    gross_income: float,
    pension_contrib_annual: float,
    is_scotland: bool,
    tax: TaxConfig = TAX
) -> float:
    """
    Simplified PAYE-style income tax for employment income only.
    - Uses adjusted net income approx = gross - pension contrib (salary sacrifice-like).
    - Does not model savings/dividends/benefits/student loans.
    """
    adjusted_net = max(0.0, gross_income - pension_contrib_annual)
    pa = personal_allowance_for_income(adjusted_net, tax)
    taxable = max(0.0, adjusted_net - pa)

    bands = tax.scot_bands if is_scotland else tax.ruk_bands

    tax_due = 0.0
    lower = 0.0
    remaining = taxable

    # Bands are defined in terms of taxable income thresholds that align to total income thresholds
    # after personal allowance. We'll compute tax on taxable slices using the band uppers.
    # For rUK, the first band upper corresponds to total income threshold 50,270.
    # Taxable band upper becomes (band_upper - personal_allowance_effective).
    for band in bands:
        if remaining <= 0:
            break

        if band.upper is None:
            slice_amt = remaining
        else:
            # Convert total-income upper threshold into taxable-income upper threshold
            taxable_upper = max(0.0, band.upper - pa)
            slice_amt = max(0.0, min(remaining, taxable_upper - lower))

        if slice_amt > 0:
            tax_due += slice_amt * band.rate
            remaining -= slice_amt
            lower += slice_amt

    return tax_due


def employee_ni_due(
    gross_income: float,
    pension_contrib_annual: float,
    tax: TaxConfig = TAX
) -> float:
    """
    Simplified Class 1 employee NI:
    - NI is charged on earnings (often after salary sacrifice, depending on arrangement).
    Here we assume pension contributions reduce NI-able pay (salary sacrifice-like).
    """
    ni_pay = max(0.0, gross_income - pension_contrib_annual)
    pt = tax.ni_primary_threshold
    uel = tax.ni_upper_earnings_limit

    if ni_pay <= pt:
        return 0.0

    main_slice = min(ni_pay, uel) - pt
    addl_slice = max(0.0, ni_pay - uel)

    return max(0.0, main_slice) * tax.ni_rate_main + addl_slice * tax.ni_rate_additional


def net_annual_income(
    gross_income: float,
    pension_contrib_annual: float,
    is_scotland: bool,
) -> Dict[str, float]:
    tax_due = income_tax_due(gross_income, pension_contrib_annual, is_scotland=is_scotland)
    ni_due = employee_ni_due(gross_income, pension_contrib_annual)
    take_home = max(0.0, gross_income - pension_contrib_annual - tax_due - ni_due)
    return {
        "gross": gross_income,
        "pension": pension_contrib_annual,
        "income_tax": tax_due,
        "ni": ni_due,
        "take_home": take_home,
    }


# ---------------------------
# BUDGET / RECOMMENDATIONS
# ---------------------------

@dataclass
class Budget:
    monthly_income_net: float
    monthly_expenses: Dict[str, float]
    monthly_debt_payments: float
    monthly_goals: Dict[str, float]  # e.g., holidays, investing, emergency fund
    monthly_surplus: float


DEFAULT_BENCHMARKS = {
    # “Rules of thumb” — you can replace with data-driven benchmarks later.
    "food": 0.12,        # 12% of net
    "leisure": 0.08,     # 8% of net
    "transport": 0.10,   # 10% of net
    "utilities": 0.08,   # 8% of net
    "subscriptions": 0.03,
    "shopping": 0.06,
}

def build_spend_suggestions(net_monthly: float, expenses: Dict[str, float]) -> List[str]:
    suggestions = []
    if net_monthly <= 0:
        return ["Net monthly income is £0 — check inputs."]

    # Flag categories above benchmark and suggest a target.
    for cat, pct in DEFAULT_BENCHMARKS.items():
        if cat in expenses:
            current = expenses[cat]
            benchmark = net_monthly * pct
            if current > benchmark * 1.15:  # 15% above guideline
                target = benchmark
                delta = current - target
                suggestions.append(
                    f"**{cat.title()}** looks high (£{current:,.0f}/mo). "
                    f"A guideline target is ~£{target:,.0f}/mo; potential trim ≈ **£{delta:,.0f}/mo**."
                )

    # Generic deficit guidance
    total_expenses = sum(expenses.values())
    if total_expenses > net_monthly:
        suggestions.append(
            f"Your tracked spending (£{total_expenses:,.0f}/mo) exceeds net income (£{net_monthly:,.0f}/mo). "
            "Prioritise cutting discretionary spend (leisure/subscriptions/shopping) and renegotiating fixed bills."
        )

    if not suggestions:
        suggestions.append("Spending looks broadly within guideline ranges. Next: optimise savings/investing automation.")
    return suggestions


def tax_efficiency_suggestions(
    is_scotland: bool,
    you: Dict[str, float],
    partner: Optional[Dict[str, float]],
    has_partner: bool,
    children_count: int
) -> List[str]:
    tips = []

    tips.append(
        "Maximise **pension contributions** first (especially via **salary sacrifice** if available): "
        "it can reduce Income Tax and usually reduces employee NI on the sacrificed amount."
    )
    tips.append(
        f"Use **ISAs** for long-term investing where suitable: up to **£{TAX.isa_allowance:,.0f} per adult per tax year** "
        "is sheltered from UK Income Tax and CGT."
    )
    tips.append(
        "If you’re a couple, consider **spreading taxable investments** across both partners (where legally owned) "
        "to use both people’s allowances/bands (e.g., ISA allowances, CGT annual exempt amount, dividend allowance)."
    )
    tips.append(
        f"If one partner earns under the Personal Allowance, check **Marriage Allowance** eligibility "
        f"(transfer up to **£{TAX.marriage_allowance_transfer:,.0f}** of allowance to the other partner if they’re a basic-rate taxpayer)."
    )

    if children_count > 0:
        tips.append(
            "If you claim Child Benefit and one partner’s income is high, check **High Income Child Benefit Charge**. "
            "Often, increasing pension contributions (and/or Gift Aid) can reduce ‘adjusted net income’ and mitigate the charge."
        )

    tips.append(
        "Avoid ‘tax avoidance schemes’. Focus on **mainstream tax-efficient wrappers** (pension/ISA), "
        "accurate records, and reliefs you’re genuinely eligible for."
    )

    # Scotland note
    if is_scotland:
        tips.append("Scotland has different income tax bands. Pension/ISA strategies still generally apply, but band edges differ.")

    # Add simple “next best pound” ordering
    tips.append(
        "**A simple ordering for most households:** "
        "1) clear expensive debt, 2) build emergency fund, 3) get employer pension match, "
        "4) increase pension/ISA investing, 5) taxable investing last."
    )
    return tips


# ---------------------------
# STREAMLIT UI
# ---------------------------

st.set_page_config(page_title="UK Wealth Planner (Prototype)", layout="wide")
st.title("UK Wealth Management Planner (UK Residents) — Prototype")

st.caption(
    "Planning tool only (not advice). Tax rules change and can be complex; verify with HMRC and/or a professional."
)

with st.sidebar:
    st.header("Household")
    nation = st.selectbox("Where do you pay income tax?", ["England/Wales/Northern Ireland", "Scotland"])
    is_scotland = nation == "Scotland"

    has_partner = st.checkbox("I have a partner (joint planning)", value=True)
    children = st.number_input("Number of children", min_value=0, max_value=10, value=0, step=1)

    st.divider()
    st.header("Income (annual)")
    you_gross = st.number_input("Your gross salary (£/year)", min_value=0.0, value=65000.0, step=1000.0)
    you_pension = st.number_input("Your pension contribution (£/year)", min_value=0.0, value=6000.0, step=250.0)

    partner_gross = 0.0
    partner_pension = 0.0
    if has_partner:
        partner_gross = st.number_input("Partner gross salary (£/year)", min_value=0.0, value=42000.0, step=1000.0)
        partner_pension = st.number_input("Partner pension contribution (£/year)", min_value=0.0, value=3000.0, step=250.0)

    st.divider()
    st.header("Debt / Housing (monthly)")
    mortgage = st.number_input("Mortgage payment (£/mo)", min_value=0.0, value=1600.0, step=50.0)
    other_debt = st.number_input("Other debt payments (£/mo) (loans/credit cards)", min_value=0.0, value=300.0, step=25.0)

    st.divider()
    st.header("Spending (monthly)")
    food = st.number_input("Food & groceries (£/mo)", min_value=0.0, value=650.0, step=25.0)
    leisure = st.number_input("Leisure / eating out / hobbies (£/mo)", min_value=0.0, value=450.0, step=25.0)
    transport = st.number_input("Transport (£/mo)", min_value=0.0, value=350.0, step=25.0)
    utilities = st.number_input("Utilities (gas/elec/water/internet) (£/mo)", min_value=0.0, value=320.0, step=25.0)
    subscriptions = st.number_input("Subscriptions (£/mo)", min_value=0.0, value=55.0, step=5.0)
    shopping = st.number_input("Shopping / misc (£/mo)", min_value=0.0, value=250.0, step=25.0)

    st.divider()
    st.header("Savings goals")
    holiday_cost_each = st.number_input("Cost per family holiday (£)", min_value=0.0, value=2500.0, step=100.0)
    holidays_per_year = st.number_input("Holidays per year", min_value=0, max_value=6, value=2, step=1)

    emergency_months = st.slider("Emergency fund target (months of essential costs)", 0, 12, 3)
    invest_monthly_target = st.number_input("Investing target (£/mo)", min_value=0.0, value=500.0, step=25.0)

    st.divider()
    st.header("Investing style")
    risk = st.select_slider("Risk profile", options=["Cautious", "Balanced", "Growth"], value="Balanced")


you = net_annual_income(you_gross, you_pension, is_scotland=is_scotland)
partner = None
if has_partner:
    partner = net_annual_income(partner_gross, partner_pension, is_scotland=is_scotland)

household_take_home_annual = you["take_home"] + (partner["take_home"] if partner else 0.0)
household_take_home_monthly = household_take_home_annual / 12.0

expenses = {
    "mortgage": mortgage,
    "other_debt": other_debt,
    "food": food,
    "leisure": leisure,
    "transport": transport,
    "utilities": utilities,
    "subscriptions": subscriptions,
    "shopping": shopping,
}

holiday_sinking_fund_monthly = (holiday_cost_each * holidays_per_year) / 12.0

# Essentials approximation: mortgage + utilities + food + transport + debt minimums
essential_monthly = mortgage + utilities + food + transport + other_debt
emergency_fund_target = essential_monthly * emergency_months

goals = {
    "holidays_sinking_fund": holiday_sinking_fund_monthly,
    "investing_target": invest_monthly_target,
}

total_outgoings = sum(expenses.values()) + sum(goals.values())
surplus = household_take_home_monthly - total_outgoings

budget = Budget(
    monthly_income_net=household_take_home_monthly,
    monthly_expenses=expenses,
    monthly_debt_payments=mortgage + other_debt,
    monthly_goals=goals,
    monthly_surplus=surplus
)

left, right = st.columns([1.15, 0.85], gap="large")

with left:
    st.subheader("1) Income & Tax (estimate)")
    c1, c2 = st.columns(2)

    with c1:
        st.markdown("**You**")
        st.write(f"Gross: £{you['gross']:,.0f}/yr")
        st.write(f"Pension: £{you['pension']:,.0f}/yr")
        st.write(f"Income Tax: £{you['income_tax']:,.0f}/yr")
        st.write(f"NI: £{you['ni']:,.0f}/yr")
        st.success(f"Take-home: £{you['take_home']:,.0f}/yr")

    with c2:
        if partner:
            st.markdown("**Partner**")
            st.write(f"Gross: £{partner['gross']:,.0f}/yr")
            st.write(f"Pension: £{partner['pension']:,.0f}/yr")
            st.write(f"Income Tax: £{partner['income_tax']:,.0f}/yr")
            st.write(f"NI: £{partner['ni']:,.0f}/yr")
            st.success(f"Take-home: £{partner['take_home']:,.0f}/yr")
        else:
            st.info("Partner not included.")

    st.divider()
    st.subheader("2) Monthly plan")

    k1, k2, k3 = st.columns(3)
    k1.metric("Net income (monthly)", f"£{budget.monthly_income_net:,.0f}")
    k2.metric("Total outgoings (monthly)", f"£{total_outgoings:,.0f}")
    k3.metric("Surplus / Deficit", f"£{budget.monthly_surplus:,.0f}")

    st.caption("Outgoings = spending + holiday sinking fund + investing target (as entered).")

    st.divider()
    st.subheader("3) Spending trim suggestions")
    trim_suggestions = build_spend_suggestions(budget.monthly_income_net, expenses)
    for s in trim_suggestions:
        st.write("• " + s)

with right:
    st.subheader("Goals & next steps")

    st.markdown("**Holiday sinking fund**")
    st.write(f"To fund **{holidays_per_year}** holidays at £{holiday_cost_each:,.0f} each, save about **£{holiday_sinking_fund_monthly:,.0f}/mo**.")

    st.markdown("**Emergency fund**")
    st.write(
        f"Essentials estimate: **£{essential_monthly:,.0f}/mo** → "
        f"target ({emergency_months} months): **£{emergency_fund_target:,.0f}**."
    )

    st.divider()
    st.subheader("Tax-efficient investing ideas (general, not advice)")
    tips = tax_efficiency_suggestions(is_scotland, you, partner, has_partner, int(children))
    for t in tips:
        st.write("• " + t)

    st.divider()
    st.subheader("Simple strategy suggestion")
    if risk == "Cautious":
        st.write("• Consider a higher allocation to high-quality bonds/cash-like funds, with equities for long-term goals.")
    elif risk == "Balanced":
        st.write("• Consider a diversified global index approach (mix of equities + bonds) inside ISA/pension wrappers.")
    else:
        st.write("• Consider higher equity allocation for long horizons, diversified globally, using ISA/pension wrappers first.")

    st.caption(
        "If you want this to generate actual model portfolios, add fund/ETF lists, glidepaths, and suitability checks."
    )

st.divider()
st.subheader("What to build next (recommended)")
st.write(
    "• Add **cashflow timeline** (month-by-month), debt amortisation, and scenario tests (rate rises, income changes).\n"
    "• Add **Child Benefit / HICBC**, student loans, dividends/interest, rental income.\n"
    "• Add data-backed spend benchmarks by household size + region.\n"
    "• Add export (CSV/PDF), user accounts, encryption, and an audit trail for assumptions."
)
