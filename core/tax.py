from dataclasses import dataclass
from typing import Literal


Nation = Literal["England/Wales/Northern Ireland", "Scotland"]


@dataclass
class TaxBreakdown:
    gross_income: float
    taxable_income: float
    personal_allowance: float
    income_tax: float
    employee_ni: float
    dividend_tax: float
    savings_tax: float
    hicbc: float
    total_tax: float
    net_income: float


def _clamp(x: float, lo: float = 0.0) -> float:
    return max(lo, float(x))


def personal_allowance(total_income: float) -> float:
    """
    Standard UK personal allowance with taper:
    - Standard PA = 12,570
    - Reduced by £1 for every £2 over £100,000
    - Reaches 0 at £125,140
    """
    total_income = _clamp(total_income)
    pa = 12570.0
    if total_income <= 100000:
        return pa
    reduction = (total_income - 100000.0) / 2.0
    return max(0.0, pa - reduction)


def income_tax_england_wales_ni(taxable_non_savings: float) -> float:
    """
    2025/26 style bands commonly used for England/Wales/NI:
    - 20% basic up to 50,270
    - 40% higher up to 125,140
    - 45% additional above
    (Taxable thresholds assume PA already removed.)
    """
    x = _clamp(taxable_non_savings)

    # taxable band limits (NOT including personal allowance)
    basic_limit = 50270.0 - 12570.0  # 37,700 taxable
    higher_limit = 125140.0 - 12570.0  # 112,570 taxable

    tax = 0.0
    # Basic
    b = min(x, basic_limit)
    tax += b * 0.20
    x -= b
    if x <= 0:
        return tax

    # Higher
    h = min(x, higher_limit - basic_limit)
    tax += h * 0.40
    x -= h
    if x <= 0:
        return tax

    # Additional
    tax += x * 0.45
    return tax


def income_tax_scotland(taxable_non_savings: float) -> float:
    """
    Scottish Income Tax 2025/26 (rates/bands for non-savings income).
    Bands here are expressed as taxable income amounts AFTER Personal Allowance.
    Source: Scottish Government / GOV.UK Scottish income tax page.
    """
    x = _clamp(taxable_non_savings)

    # taxable bands (after PA)
    # Starter: 12,571–15,397 => 2,826 taxable
    # Basic:   15,398–27,491 => 12,094 taxable
    # Inter:   27,492–43,662 => 16,171 taxable
    # Higher:  43,663–75,000 => 31,338 taxable
    # Advanced:75,001–125,140 => 50,140 taxable
    bands = [
        (2826.0, 0.19),
        (12094.0, 0.20),
        (16171.0, 0.21),
        (31338.0, 0.42),
        (50140.0, 0.45),
        (float("inf"), 0.48),
    ]

    tax = 0.0
    for width, rate in bands:
        portion = min(x, width)
        tax += portion * rate
        x -= portion
        if x <= 0:
            break
    return tax


def employee_class1_ni(earnings: float) -> float:
    """
    Simplified employee NI (Class 1) using the common structure:
    - 8% from Primary Threshold to Upper Earnings Limit
    - 2% above UEL
    Thresholds vary by year; we use annualised typical values aligned with payroll tables.
    """
    e = _clamp(earnings)
    # Common annualised figures used in payroll guidance (approx)
    pt = 12570.0
    uel = 50270.0

    if e <= pt:
        return 0.0
    main_band = min(e, uel) - pt
    above = max(0.0, e - uel)
    return (main_band * 0.08) + (above * 0.02)


def dividend_tax(dividends: float, taxable_band_income: float, nation: Nation) -> float:
    """
    Simplified dividend tax:
    - Dividend allowance assumed (£500)
    - Rates by band (common UK dividend rates): 8.75%, 33.75%, 39.35%
    Scotland uses UK dividend rates (dividends are not subject to Scottish rates).
    taxable_band_income: taxable income already used by non-savings (after PA).
    """
    div = _clamp(dividends)
    if div <= 0:
        return 0.0

    allowance = 500.0
    div_taxable = max(0.0, div - allowance)

    # Band edges based on UK thresholds (still used for dividend bands)
    basic_limit = 50270.0 - 12570.0  # 37,700 taxable
    higher_limit = 125140.0 - 12570.0  # 112,570 taxable

    remaining_basic = max(0.0, basic_limit - taxable_band_income)
    remaining_higher = max(0.0, higher_limit - max(taxable_band_income, basic_limit))

    tax = 0.0
    b = min(div_taxable, remaining_basic)
    tax += b * 0.0875
    div_taxable -= b
    if div_taxable <= 0:
        return tax

    h = min(div_taxable, remaining_higher)
    tax += h * 0.3375
    div_taxable -= h
    if div_taxable <= 0:
        return tax

    tax += div_taxable * 0.3935
    return tax


def savings_interest_tax(interest: float, total_income_for_band: float, nation: Nation) -> float:
    """
    Simplified savings interest tax:
    - Uses Personal Savings Allowance (PSA):
      * £1,000 basic-rate
      * £500 higher-rate
      * £0 additional-rate
    (Ignoring starting rate for savings for simplicity.)
    """
    i = _clamp(interest)
    if i <= 0:
        return 0.0

    # Determine band using UK thresholds
    if total_income_for_band <= 50270.0:
        psa = 1000.0
        rate = 0.20
    elif total_income_for_band <= 125140.0:
        psa = 500.0
        rate = 0.40
    else:
        psa = 0.0
        rate = 0.45

    taxable = max(0.0, i - psa)
    return taxable * rate


def hicbc_charge(child_benefit_received: float, highest_parent_adjusted_net: float) -> float:
    """
    High Income Child Benefit Charge (HICBC) estimate:
    - If adjusted net <= 60,000: 0
    - If >= 80,000: 100% of benefit (in this simplified engine)
    - Otherwise: proportional
    NOTE: Thresholds can change; keep as editable logic.
    """
    benefit = _clamp(child_benefit_received)
    if benefit <= 0:
        return 0.0

    inc = _clamp(highest_parent_adjusted_net)
    lower = 60000.0
    upper = 80000.0

    if inc <= lower:
        return 0.0
    if inc >= upper:
        return benefit

    proportion = (inc - lower) / (upper - lower)
    return benefit * proportion


def estimate_tax(
    nation: Nation,
    employment_income: float,
    pension_employee: float,
    savings_interest: float,
    dividends: float,
    rental_income: float,
    child_benefit_received: float,
    highest_parent_adjusted_net_for_hicbc: float,
) -> TaxBreakdown:
    """
    Core estimator used by app.py.
    - employment_income includes salary + bonus (gross)
    - employee pension reduces taxable income (proxy for pension relief/salary sacrifice)
    - savings interest, dividends, rental treated as additional income
    """
    employment_income = _clamp(employment_income)
    pension_employee = _clamp(pension_employee)
    savings_interest = _clamp(savings_interest)
    dividends = _clamp(dividends)
    rental_income = _clamp(rental_income)

    gross_total = employment_income + savings_interest + dividends + rental_income
    # Proxy taxable: remove employee pension (planner approximation)
    income_for_pa = max(0.0, gross_total - pension_employee)

    pa = personal_allowance(income_for_pa)

    # Split: treat everything except dividends/interest as "non-savings"
    non_savings = max(0.0, employment_income + rental_income - pension_employee)
    taxable_non_savings = max(0.0, non_savings - pa)

    if nation == "Scotland":
        it = income_tax_scotland(taxable_non_savings)
    else:
        it = income_tax_england_wales_ni(taxable_non_savings)

    ni = employee_class1_ni(employment_income)

    # For dividend banding, use taxable_non_savings as “band used”
    div_tax = dividend_tax(dividends, taxable_non_savings, nation)

    # For savings banding, use total income (before PA) to determine PSA band
    sav_tax = savings_interest_tax(savings_interest, income_for_pa, nation)

    hicbc = hicbc_charge(child_benefit_received, highest_parent_adjusted_net_for_hicbc)

    total_tax = it + ni + div_tax + sav_tax + hicbc
    net = gross_total - total_tax

    return TaxBreakdown(
        gross_income=gross_total,
        taxable_income=income_for_pa,
        personal_allowance=pa,
        income_tax=it,
        employee_ni=ni,
        dividend_tax=div_tax,
        savings_tax=sav_tax,
        hicbc=hicbc,
        total_tax=total_tax,
        net_income=net,
    )
