from typing import List, Dict
from .models import ChildCareChild


def england_eligible_100k(highest_parent_adj_net: float) -> bool:
    # Planner proxy: if any parent > £100k, assume not eligible for certain childcare support.
    return float(highest_parent_adj_net) <= 100000.0


def funded_hours_england(age_years: float, eligible_working: bool) -> float:
    """
    Simplified weekly funded hours (planner-level):
    - Age 3–4: universal 15; working offer can be 30
    - Under 3: assume 0 unless working offer (simplified)
    """
    age_years = float(age_years)
    if 3.0 <= age_years < 5.0:
        return 30.0 if eligible_working else 15.0
    if 0.75 <= age_years < 3.0:
        return 30.0 if eligible_working else 0.0
    return 0.0


def funded_hours_scotland(age_years: float) -> float:
    """
    Simplified: treat 3–4 as ~30 hours/week term-time equivalent (planner assumption).
    """
    age_years = float(age_years)
    if 3.0 <= age_years < 5.0:
        return 30.0
    return 0.0


def estimate_childcare(
    nation: str,
    highest_parent_adj_net: float,
    children: List[ChildCareChild],
) -> Dict[str, float]:
    """
    Returns annual gross cost, funded value, TFC top-up, net annual, net monthly.
    This is a planner estimate – not authoritative.
    """
    if not children:
        return dict(
            gross_annual=0.0,
            funded_value_annual=0.0,
            tfc_topup_annual=0.0,
            net_annual=0.0,
            net_monthly=0.0,
            england_100k_blocked=False,
        )

    is_scotland = (nation == "Scotland")
    eligible_eng = (not is_scotland) and england_eligible_100k(highest_parent_adj_net)

    gross_annual = 0.0
    funded_value_annual = 0.0
    tfc_topup_annual = 0.0

    for child in children:
        age = float(child.age_years)
        days = max(0.0, float(child.days_per_week))
        hrs_day = max(0.0, float(child.hours_per_day))
        cost_day = max(0.0, float(child.cost_per_day))

        annual_days = days * 52.0
        gross = annual_days * cost_day
        gross_annual += gross

        # convert day cost to hourly approximation
        cost_hr = (cost_day / hrs_day) if hrs_day > 0 else 0.0
        attendance_hours_week = days * hrs_day

        fh = funded_hours_scotland(age) if is_scotland else funded_hours_england(age, eligible_eng)
        funded_hours_used = min(fh, attendance_hours_week)

        # funded often term-time; use 38 weeks as a planner approximation
        funded_value = funded_hours_used * cost_hr * 38.0
        funded_value_annual += max(0.0, funded_value)

        # Tax-Free Childcare (very simplified):
        # top-up = 20% capped at £2k per child per year
        tfc_ok = (float(highest_parent_adj_net) <= 100000.0) and (age < 11.0)
        net_after_funded = max(0.0, gross - funded_value)
        if tfc_ok:
            tfc_topup_annual += min(net_after_funded * 0.20, 2000.0)

    net_annual = max(0.0, gross_annual - funded_value_annual - tfc_topup_annual)

    return dict(
        gross_annual=gross_annual,
        funded_value_annual=funded_value_annual,
        tfc_topup_annual=tfc_topup_annual,
        net_annual=net_annual,
        net_monthly=net_annual / 12.0,
        england_100k_blocked=(not is_scotland) and (float(highest_parent_adj_net) > 100000.0),
    )
