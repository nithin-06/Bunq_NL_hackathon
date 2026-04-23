"""
intervention.py — Proactively intervene before a bad financial decision executes.

This module sits BETWEEN intent parsing and execution.
It evaluates the proposed action against the user's financial state
and either clears it, warns, or blocks it.

Called by main.py before any bank action runs.
"""

from llm import query_json, REASONING_MODEL
import mock_bank

# ---------------------------------------------------------------------------
# Risk thresholds (tunable)
# ---------------------------------------------------------------------------

# Block if investment would leave checking below this
MINIMUM_SAFE_BALANCE = 500.0

# Warn if investing more than this % of checking balance at once
LARGE_INVESTMENT_RATIO = 0.4  # 40%

# Block crypto if it exceeds this % of total portfolio
MAX_CRYPTO_PORTFOLIO_RATIO = 0.15  # 15%


# ---------------------------------------------------------------------------
# Rule-based fast checks (no LLM needed, deterministic)
# ---------------------------------------------------------------------------

def _rule_based_checks(action: dict, balances: dict) -> dict | None:
    """
    Hard rule checks. Returns intervention dict if triggered, else None.
    These run before the LLM to catch obvious cases instantly.
    """
    amount = action.get("amount") or 0.0
    instrument = action.get("instrument", "")
    intent = action.get("intent", "")
    checking = balances["checking"]

    if intent not in ("invest", "save"):
        return None

    # Rule 1: Would drop balance below minimum safe threshold
    if checking - amount < MINIMUM_SAFE_BALANCE:
        shortfall = amount - (checking - MINIMUM_SAFE_BALANCE)
        return {
            "level": "block",
            "triggered": True,
            "reason": (
                f"This would leave only €{checking - amount:.2f} in your account. "
                f"We recommend keeping at least €{MINIMUM_SAFE_BALANCE:.2f} as a buffer. "
                f"Maximum safe amount to {intent}: €{checking - MINIMUM_SAFE_BALANCE:.2f}."
            ),
            "suggestion": f"Consider investing €{max(0, checking - MINIMUM_SAFE_BALANCE):.2f} instead.",
        }

    # Rule 2: Large single investment
    if intent == "invest" and amount > LARGE_INVESTMENT_RATIO * checking:
        pct = (amount / checking) * 100
        return {
            "level": "warn",
            "triggered": True,
            "reason": (
                f"You're about to invest {pct:.0f}% of your checking balance (€{amount:.2f} of €{checking:.2f}). "
                f"That's a large single move."
            ),
            "suggestion": "Consider splitting this into smaller investments over time (dollar-cost averaging).",
        }

    # Rule 3: Crypto concentration
    if instrument == "CRYPTO_BTC" and intent == "invest":
        total_portfolio = balances["investments"] + checking + balances["savings"]
        crypto_after = amount  # simplified: assume no existing crypto
        ratio = crypto_after / total_portfolio
        if ratio > MAX_CRYPTO_PORTFOLIO_RATIO:
            return {
                "level": "warn",
                "triggered": True,
                "reason": (
                    f"Investing €{amount:.2f} in crypto would be {ratio*100:.0f}% of your total net worth. "
                    f"Recommended maximum is {MAX_CRYPTO_PORTFOLIO_RATIO*100:.0f}%."
                ),
                "suggestion": f"Limit crypto to €{total_portfolio * MAX_CRYPTO_PORTFOLIO_RATIO:.2f} or less.",
            }

    return None


# ---------------------------------------------------------------------------
# LLM-based deeper reasoning
# ---------------------------------------------------------------------------

INTERVENTION_PROMPT = """You are a cautious financial advisor reviewing a proposed action.

User's financial state:
- Checking: €{checking:.2f}
- Savings: €{savings:.2f}
- Investments: €{investments:.2f}
- Recent transactions: {recent_tx}

Proposed action:
- Intent: {intent}
- Amount: €{amount:.2f}
- Instrument: {instrument}

Assess if this is a risky or unwise financial decision.
Consider: emergency fund adequacy, concentration risk, timing, and general financial health.

Respond ONLY with JSON:
{{
  "level": "clear" | "warn" | "block",
  "triggered": true | false,
  "reason": "<explanation if warn or block, empty string if clear>",
  "suggestion": "<alternative suggestion if warn or block, empty string if clear>"
}}

Be practical. Don't block reasonable decisions. Only flag genuinely risky ones."""


def _llm_risk_check(action: dict, balances: dict) -> dict:
    """
    LLM-based risk assessment for nuanced cases.
    """
    recent = mock_bank.get_recent_transactions(limit=3)
    recent_summary = ", ".join(
        f"{t['type']} €{float(t.get('amount', 0) or 0):.2f}" for t in recent
    ) or "none"

    prompt = INTERVENTION_PROMPT.format(
        checking=balances["checking"],
        savings=balances["savings"],
        investments=balances["investments"],
        recent_tx=recent_summary,
        intent=action.get("intent", "unknown"),
        amount=action.get("amount") or 0.0,
        instrument=action.get("instrument") or "N/A",
    )

    result = query_json(prompt, model=REASONING_MODEL)

    # Sanitize
    result.setdefault("level", "clear")
    result.setdefault("triggered", False)
    result.setdefault("reason", "")
    result.setdefault("suggestion", "")

    if result["level"] not in ("clear", "warn", "block"):
        result["level"] = "clear"

    return result


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def evaluate(action: dict) -> dict:
    """
    Evaluate a proposed financial action before it executes.

    Args:
        action: dict with keys: intent, amount, instrument
                (same shape as voice_invest.parse_investment_intent output)

    Returns:
        {
            "level": "clear" | "warn" | "block",
            "triggered": bool,
            "reason": str,
            "suggestion": str,
            "proceed": bool    ← True only if level == "clear"
        }
    """
    balances = mock_bank.get_balance()

    # Fast rule checks first (no LLM latency)
    rule_result = _rule_based_checks(action, balances)

    if rule_result and rule_result["level"] == "block":
        rule_result["proceed"] = False
        return rule_result

    # LLM deeper check
    llm_result = _llm_risk_check(action, balances)

    # Merge: take the stricter of the two
    if rule_result and rule_result["level"] == "warn" and llm_result["level"] == "clear":
        final = rule_result
    else:
        final = llm_result

    final["proceed"] = final["level"] == "clear"
    return final


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    test_actions = [
        {"intent": "invest", "amount": 3900.0, "instrument": "ETF_SP500"},   # should block (drops below buffer)
        {"intent": "invest", "amount": 500.0, "instrument": "ETF_SP500"},    # should clear
        {"intent": "invest", "amount": 2000.0, "instrument": "CRYPTO_BTC"},  # should warn
    ]

    for action in test_actions:
        print(f"\n--- Testing: {action} ---")
        result = evaluate(action)
        print(json.dumps(result, indent=2))