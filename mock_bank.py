"""
mock_bank.py — Simulated banking wrapper.

We don't have the real bank's codebase, so this simulates the API surface
a real integration would call. Every function prints what it's "doing" so
the demo is clear, then returns structured data as if a real bank responded.

In production: replace each function body with the actual SDK/API call.
"""

import random
from datetime import datetime

# ---------------------------------------------------------------------------
# Simulated account state (in-memory, resets each run)
# ---------------------------------------------------------------------------
_STATE = {
    "balance": 4_250.00,
    "savings": 1_800.00,
    "investment_portfolio": {
        "ETF_SP500": {"units": 5.2, "price_per_unit": 410.0},
        "ETF_BONDS": {"units": 10.0, "price_per_unit": 95.0},
    },
    "transaction_log": [],
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_balance() -> dict:
    """Return current account balances."""
    portfolio_value = sum(
        v["units"] * v["price_per_unit"]
        for v in _STATE["investment_portfolio"].values()
    )
    return {
        "checking": _STATE["balance"],
        "savings": _STATE["savings"],
        "investments": round(portfolio_value, 2),
        "currency": "EUR",
    }


def get_recent_transactions(limit: int = 5) -> list[dict]:
    """Return the most recent transactions."""
    return _STATE["transaction_log"][-limit:]


def invest(amount: float, instrument: str = "ETF_SP500") -> dict:
    """
    Execute a mock investment order.

    Args:
        amount: Amount in EUR to invest.
        instrument: Which instrument to buy (ETF_SP500, ETF_BONDS, CRYPTO_BTC).

    Returns:
        Transaction receipt dict.
    """
    if amount <= 0:
        return {"success": False, "reason": "Amount must be positive."}

    if amount > _STATE["balance"]:
        return {
            "success": False,
            "reason": f"Insufficient funds. Balance: €{_STATE['balance']:.2f}, requested: €{amount:.2f}",
        }

    # Simulate price + units purchased
    prices = {"ETF_SP500": 410.0, "ETF_BONDS": 95.0, "CRYPTO_BTC": 62_000.0}
    price = prices.get(instrument, 100.0)
    units = round(amount / price, 4)

    # Deduct from balance
    _STATE["balance"] -= amount
    _STATE["balance"] = round(_STATE["balance"], 2)

    # Add to portfolio
    if instrument not in _STATE["investment_portfolio"]:
        _STATE["investment_portfolio"][instrument] = {"units": 0, "price_per_unit": price}
    _STATE["investment_portfolio"][instrument]["units"] += units
    _STATE["investment_portfolio"][instrument]["units"] = round(
        _STATE["investment_portfolio"][instrument]["units"], 4
    )

    tx = {
        "type": "investment",
        "instrument": instrument,
        "amount": amount,
        "units_purchased": units,
        "price_per_unit": price,
        "timestamp": datetime.now().isoformat(),
        "new_balance": _STATE["balance"],
    }
    _STATE["transaction_log"].append(tx)

    print(f"[BANK] ✅ Invested €{amount:.2f} → {units} units of {instrument} @ €{price:.2f}/unit")
    return {"success": True, "transaction": tx}


def transfer_to_savings(amount: float) -> dict:
    """Move money from checking to savings."""
    if amount > _STATE["balance"]:
        return {"success": False, "reason": "Insufficient funds in checking."}

    _STATE["balance"] -= amount
    _STATE["savings"] += amount
    _STATE["balance"] = round(_STATE["balance"], 2)
    _STATE["savings"] = round(_STATE["savings"], 2)

    tx = {
        "type": "savings_transfer",
        "amount": amount,
        "timestamp": datetime.now().isoformat(),
        "new_checking": _STATE["balance"],
        "new_savings": _STATE["savings"],
    }
    _STATE["transaction_log"].append(tx)

    print(f"[BANK] ✅ Transferred €{amount:.2f} to savings")
    return {"success": True, "transaction": tx}


def log_expense(merchant: str, amount: float, category: str) -> dict:
    """Log a receipt-based expense (called by receipt pipeline)."""
    tx = {
        "type": "expense",
        "merchant": merchant,
        "amount": amount,
        "category": category,
        "timestamp": datetime.now().isoformat(),
    }
    _STATE["transaction_log"].append(tx)
    print(f"[BANK] ✅ Logged €{amount:.2f} expense at {merchant} [{category}]")
    return {"success": True, "transaction": tx}