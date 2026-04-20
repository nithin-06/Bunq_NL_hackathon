"""
insight.py — Generate contextual insight and risk flags for a classified expense.
"""

from dataclasses import dataclass, field
from typing import Optional


# --- Default weekly budget thresholds per category (EUR) ---
DEFAULT_BUDGETS = {
    "groceries": 80.0,
    "dining": 40.0,
    "travel": 60.0,
    "utilities": 100.0,
    "shopping": 50.0,
}

# Spike threshold: if single expense > X% of weekly budget, flag it
SPIKE_RATIO = 0.5  # 50%


@dataclass
class UserContext:
    """Tracks spending state passed in from the calling app / session."""
    weekly_spent: dict = field(default_factory=lambda: {cat: 0.0 for cat in DEFAULT_BUDGETS})
    weekly_budgets: dict = field(default_factory=lambda: dict(DEFAULT_BUDGETS))
    currency: str = "€"


def _budget_status(
    category: str,
    amount: float,
    ctx: UserContext,
) -> tuple[str, bool]:
    """
    Returns (status_message, is_risk).
    """
    budget = ctx.weekly_budgets.get(category, DEFAULT_BUDGETS.get(category, 100.0))
    spent_so_far = ctx.weekly_spent.get(category, 0.0)
    new_total = spent_so_far + amount
    remaining = budget - new_total

    is_spike = amount >= SPIKE_RATIO * budget
    is_over = new_total > budget

    if is_over:
        overage = new_total - budget
        return (
            f"You've exceeded your weekly {category} budget by {ctx.currency}{overage:.2f}.",
            True,
        )
    elif is_spike:
        return (
            f"Large {category} expense — {ctx.currency}{amount:.2f} is over half your weekly budget.",
            True,
        )
    elif remaining < budget * 0.2:
        return (
            f"You're close to your weekly {category} limit. Only {ctx.currency}{remaining:.2f} left.",
            False,
        )
    else:
        return (
            f"You're within your weekly {category} budget. {ctx.currency}{remaining:.2f} remaining.",
            False,
        )


def generate_insight(
    parsed_data: dict,
    category: str,
    user_context: Optional[UserContext] = None,
) -> dict:
    """
    Generate a human-readable insight message + risk flag.

    Args:
        parsed_data: Output from parser.parse_receipt()
        category: Classification output from classifier
        user_context: Optional spending state for budget comparisons

    Returns:
        {
            "message": str,
            "risk": bool,
            "risk_reason": str | None
        }
    """
    ctx = user_context or UserContext()
    amount = parsed_data.get("total") or 0.0
    merchant = parsed_data.get("merchant", "Unknown")
    currency = ctx.currency

    status_msg, is_risk = _budget_status(category, amount, ctx)

    # Build main message
    message = (
        f"Added {category} expense of {currency}{amount:.2f} at {merchant}. "
        f"{status_msg}"
    )

    return {
        "message": message,
        "risk": is_risk,
        "risk_reason": status_msg if is_risk else None,
    }


# --- quick test ---
if __name__ == "__main__":
    ctx = UserContext(weekly_spent={"groceries": 65.0})
    data = {"merchant": "Albert Heijn", "items": ["Milk", "Bread"], "total": 22.50}
    result = generate_insight(data, "groceries", ctx)
    import json
    print(json.dumps(result, indent=2))