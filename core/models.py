from dataclasses import dataclass
from typing import Optional, List


@dataclass
class Debt:
    name: str
    balance: float
    apr_pct: float
    monthly_payment: float
    end_date: Optional[str] = None  # YYYY-MM-DD


@dataclass
class ChildCareChild:
    age_years: float
    days_per_week: float
    hours_per_day: float
    cost_per_day: float


@dataclass
class HouseholdInput:
    nation: str
    has_partner: bool
    children_count: int

    # Income
    you_salary_annual: float
    you_bonus_mode: str
    you_bonus_amount: float

    partner_salary_annual: float
    partner_bonus_mode: str
    partner_bonus_amount: float

    # Pension
    mpaa_triggered: bool
    you_employee_pension_annual: float
    you_employer_pension_annual: float
    partner_employee_pension_annual: float
    partner_employer_pension_annual: float

    # Savings & other income
    savings_balance: float
    savings_interest_pct: float
    dividends_annual: float
    rental_net_annual: float

    # Mortgage
    mortgage_balance: float
    mortgage_rate_pct: float
    mortgage_payment_monthly: float
    mortgage_term_years: int

    # Other debts
    debts: List[Debt]

    # Spend
    food: float
    leisure: float
    transport: float
    utilities: float
    subscriptions: float
    shopping: float

    # Goals
    holiday_cost_each: float
    holidays_per_year: int

    # Emergency fund
    emergency_target_months: int
    emergency_current_balance: float
    emergency_build_months: int

    # Investing
    risk_profile_
