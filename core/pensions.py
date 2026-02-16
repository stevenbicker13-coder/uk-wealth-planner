STD_ANNUAL_ALLOWANCE = 60000.0
TAPER_TI = 200000.0
TAPER_AI = 260000.0
TAPER_MIN = 10000.0
MPAA_LIMIT = 10000.0


def annual_bonus_to_annual(mode: str, amount: float) -> float:
    """Convert bonus entry into annual total."""
    if mode == "Annual":
        return float(amount)
    if mode == "Quarterly":
        return float(amount) * 4.0
    return 0.0


def estimate_annual_allowance(
    employment_gross: float,
    employee_contrib: float,
    employer_contrib: float,
    mpaa_triggered: bool,
) -> float:
    """
    Planner-level estimate (not authoritative):
    - Standard AA = £60k
    - If threshold income > £200k AND adjusted income > £260k, taper £1 per £2 above £260k to min £10k
    - If MPAA triggered, cap to £10k
    - Does not model carry-forward.
    """
    employment_gross = max(0.0, float(employment_gross))
    employee_contrib = max(0.0, float(employee_contrib))
    employer_contrib = max(0.0, float(employer_contrib))

    threshold_income = max(0.0, employment_gross - employee_contrib)
    adjusted_income = max(0.0, employment_gross + employer_contrib)

    aa = STD_ANNUAL_ALLOWANCE

    if threshold_income > TAPER_TI and adjusted_income > TAPER_AI:
        reduction = (adjusted_income - TAPER_AI) / 2.0
        aa = max(TAPER_MIN, STD_ANNUAL_ALLOWANCE - reduction)

    if mpaa_triggered:
        aa = min(aa, MPAA_LIMIT)

    # Guardrail: pension input for relief can't exceed earnings (simplified)
    aa = min(aa, employment_gross)

    return max(0.0, aa)


def cap_employee_contrib(allowance: float, employer_contrib: float, desired_employee: float) -> float:
    """
    Total pension input includes employer + employee.
    Employee max = allowance - employer input.
    """
    allowance = max(0.0, float(allowance))
    employer_contrib = max(0.0, float(employer_contrib))
    desired_employee = max(0.0, float(desired_employee))

    employee_max = max(0.0, allowance - employer_contrib)
    return min(desired_employee, employee_max)
