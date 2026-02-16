from dataclasses import dataclass
from typing import Optional


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
