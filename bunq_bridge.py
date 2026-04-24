"""
bunq_bridge.py — Live bunq integration layer.

Drop-in replacement for mock_bank.py — same function signatures,
but calls real bunq sandbox API instead of reading/writing CSVs.

The bridge also MIRRORS every bunq action into the local CSV via mock_bank,
so the dashboard always has data even if bunq is slow.

Architecture:
    pipeline / main.py
         ↓ calls
    bunq_bridge  (this file)
         ↓ calls both
    bunq_client  (real API) + mock_bank (local CSV mirror)

Usage:
    In main.py, swap:
        import mock_bank as bank
    for:
        import bunq_bridge as bank

    Everything else stays identical.

Setup required:
    1. python bunq_client.py setup    ← run once
    2. Set BUNQ_API_KEY env var or paste key into bunq_client.py
    3. python bunq_client.py topup    ← get sandbox money
"""

import json
from datetime import datetime
from pathlib import Path

# Local imports
import mock_bank                          # CSV mirror — always updated
import bunq_client as _bunq              # real API calls

# ---------------------------------------------------------------------------
# Session bootstrap — load state + keys once at import time
# ---------------------------------------------------------------------------

_state: dict = {}
_private_pem: str = ""
_account_id: int = 0          # primary monetary account ID
_initialized: bool = False


def _ensure_session():
    """Load state from disk. Refresh session if token is missing."""
    global _state, _private_pem, _account_id, _initialized

    if _initialized:
        return

    _state = _bunq._load_state()
    _private_pem, _ = _bunq._generate_or_load_keys()

    if not _state.get("session_token"):
        print("[BRIDGE] No active session found. Running setup...")
        _state, _private_pem = _bunq.full_setup()

    # Resolve primary account ID
    if _state.get("primary_account_id"):
        _account_id = int(_state["primary_account_id"])
    else:
        accounts = _bunq.get_accounts(_state)
        if not accounts:
            raise RuntimeError(
                "[BRIDGE] No monetary accounts found on bunq sandbox.\n"
                "Run: python bunq_client.py topup"
            )
        _account_id = accounts[0]["id"]
        _state["primary_account_id"] = _account_id
        _bunq._save_state(_state)

    print(f"[BRIDGE] ✅ Connected to bunq. Account ID: {_account_id}")
    _initialized = True


def _refresh_session_if_needed(exc: Exception):
    """If we got a 401, refresh the session and retry."""
    global _state, _initialized
    if "401" in str(exc) or "Unauthorized" in str(exc):
        print("[BRIDGE] Session expired — refreshing...")
        _state = _bunq.create_session(_private_pem, _state)
        _initialized = True
        return True
    return False


# ---------------------------------------------------------------------------
# Public API — mirrors mock_bank.py exactly
# ---------------------------------------------------------------------------

def get_balance() -> dict:
    """
    Fetch live balance from bunq ONLY.
    Portfolio value is kept local (bunq sandbox has no investment accounts).
    Does NOT fall back to CSV mock data on failure — returns zeros with error flag.
    """
    _ensure_session()
    try:
        accounts = _bunq.get_accounts(_state)
        if not accounts:
            raise RuntimeError("No accounts returned from bunq")

        primary = next((a for a in accounts if a["id"] == _account_id), accounts[0])
        checking = float(primary["balance"] or 0)

        # Savings = any additional bunq accounts beyond the primary
        savings = sum(
            float(a["balance"] or 0)
            for a in accounts
            if a["id"] != _account_id
        )

        # Portfolio stays local — bunq sandbox has no brokerage
        portfolio = mock_bank.get_portfolio()
        investments = sum(v["units"] * v["price_per_unit"] for v in portfolio.values())

        return {
            "checking":        round(checking, 2),
            "savings":         round(savings, 2),
            "investments":     round(investments, 2),
            "currency":        primary.get("currency", "EUR"),
            "iban":            primary.get("iban", ""),
            "email":           primary.get("email", ""),
            "bunq_account_id": _account_id,
            "source":          "bunq_live",
        }

    except Exception as e:
        print(f"[BRIDGE] ⚠️  get_balance failed: {e}")
        return {
            "checking":    0.0,
            "savings":     0.0,
            "investments": 0.0,
            "currency":    "EUR",
            "source":      "error",
            "error":       str(e),
        }


def get_portfolio() -> dict:
    """Portfolio is local only — bunq sandbox has no investment products."""
    return mock_bank.get_portfolio()


VALID_CATEGORIES = {"groceries", "dining", "travel", "utilities", "shopping", "savings", "investment"}

def _infer_category(description: str, amount: float, is_expense: bool) -> tuple[str, str]:
    """
    Recover category and merchant from the description we set when making payments.
    Format we wrote: "<category> at <merchant>"  or  "Investment: <instrument>"
    Falls back to 'income' / 'expense' for unknown patterns.
    Returns (category, merchant).
    """
    desc = description.strip().lower()

    # "investment: etf_sp500" → investment
    if desc.startswith("investment:"):
        instrument = description.split(":", 1)[1].strip().upper()
        return "investment", instrument

    # "transfer to savings" → savings
    if "savings" in desc:
        return "savings", ""

    # "groceries at jumbo" / "shopping at primark" etc.
    if " at " in desc:
        parts = description.split(" at ", 1)
        cat = parts[0].strip().lower()
        merchant = parts[1].strip().title()
        if cat in VALID_CATEGORIES:
            return cat, merchant
        return "expense", merchant

    # "voice payment to sugar daddy" / top-up requests from sugardaddy
    if not is_expense:
        return "income", description.title()

    return "bunq_payment", ""


def get_recent_transactions(limit: int = 5) -> list[dict]:
    """
    Fetch real transactions from bunq and normalise to our schema.
    Category is inferred from the payment description — not hardcoded.
    """
    _ensure_session()
    try:
        raw = _bunq.get_payments(_state, _account_id, limit=limit)
        normalised = []
        for p in raw:
            amount     = float(p["amount"])
            is_expense = amount < 0
            description = p.get("description", "")
            category, merchant = _infer_category(description, abs(amount), is_expense)

            # If counterparty name is available and we don't have a merchant yet, use it
            if not merchant:
                merchant = p.get("counterparty", "")

            tx = {
                "timestamp":       p["created"],
                "type":            "expense" if is_expense else "income",
                "merchant":        merchant,
                "category":        category,
                "instrument":      "",
                "amount":          abs(amount),
                "units_purchased": "",
                "price_per_unit":  "",
                "balance_after":   "",
                "note":            description,
            }
            normalised.append(tx)
        return normalised

    except Exception as e:
        print(f"[BRIDGE] ⚠️  get_recent_transactions failed ({e}), using CSV")
        return mock_bank.get_recent_transactions(limit)


def get_all_transactions(bunq_only: bool = True) -> list[dict]:
    """
    In live mode: returns ONLY real bunq transactions (no mock CSV data).
    Set bunq_only=False if you explicitly want the merged view.
    """
    _ensure_session()
    try:
        return get_recent_transactions(limit=50)   # pure bunq, no CSV mixing
    except Exception as e:
        print(f"[BRIDGE] ⚠️  get_all_transactions failed ({e}), using CSV fallback")
        import mock_bank as _mb
        return _mb.get_all_transactions()


def invest(amount: float, instrument: str = "ETF_SP500") -> dict:
    """
    Investments go via local portfolio (bunq sandbox has no brokerage).
    We make a real bunq PAYMENT to simulate capital allocation,
    then record it locally.
    """
    _ensure_session()

    # Simulate: transfer the investment amount to a "portfolio" account
    # In real life this would be a brokerage API call
    try:
        result = _bunq.make_payment(
            state=_state,
            private_pem=_private_pem,
            account_id=_account_id,
            amount=str(amount),
            description=f"Investment: {instrument}",
            recipient_email="sugardaddy@bunq.com",   # sandbox recipient
            recipient_name="Investment Simulation",
        )
        print(f"[BRIDGE] bunq payment executed for investment: €{amount:.2f}")
    except Exception as e:
        print(f"[BRIDGE] ⚠️  bunq invest payment failed ({e}), recording locally only")

    # Always record locally regardless
    return mock_bank.invest(amount, instrument)


def transfer_to_savings(amount: float) -> dict:
    """
    Transfer to savings — makes a real bunq payment to simulate the action,
    and mirrors locally.
    """
    _ensure_session()
    try:
        _bunq.make_payment(
            state=_state,
            private_pem=_private_pem,
            account_id=_account_id,
            amount=str(amount),
            description="Transfer to savings",
            recipient_email="sugardaddy@bunq.com",
        )
        print(f"[BRIDGE] bunq payment executed for savings: €{amount:.2f}")
    except Exception as e:
        print(f"[BRIDGE] ⚠️  bunq savings transfer failed ({e}), recording locally only")

    return mock_bank.transfer_to_savings(amount)


def log_expense(merchant: str, amount: float, category: str) -> dict:
    """
    Log a receipt expense.
    Makes a real bunq payment and mirrors to CSV.
    Amount here is what was spent on the receipt — we record it as an outgoing.
    """
    _ensure_session()
    try:
        _bunq.make_payment(
            state=_state,
            private_pem=_private_pem,
            account_id=_account_id,
            amount=str(round(amount, 2)),
            description=f"{category} at {merchant}",
            recipient_email="sugardaddy@bunq.com",
        )
        print(f"[BRIDGE] bunq payment logged: €{amount:.2f} at {merchant}")
    except Exception as e:
        print(f"[BRIDGE] ⚠️  bunq expense payment failed ({e}), recording locally only")

    return mock_bank.log_expense(merchant, amount, category)


def reset_to_defaults():
    """Reset local CSV only — bunq sandbox state persists."""
    mock_bank.reset_to_defaults()
    print("[BRIDGE] Local CSV reset. bunq sandbox balance unchanged.")


# ---------------------------------------------------------------------------
# bunq-specific extras (not in mock_bank)
# ---------------------------------------------------------------------------

def get_bunq_accounts() -> list[dict]:
    """Return all bunq accounts with full details."""
    _ensure_session()
    return _bunq.get_accounts(_state)


def get_bunq_payments(limit: int = 20) -> list[dict]:
    """Return raw bunq payment objects."""
    _ensure_session()
    return _bunq.get_payments(_state, _account_id, limit=limit)


def topup(amount: str = "500.00") -> dict:
    """Request sandbox money from sugardaddy."""
    _ensure_session()
    return _bunq.request_money_from_sugardaddy(_state, _private_pem, _account_id, amount)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "balance"

    if cmd == "balance":
        b = get_balance()
        print(f"\n  Checking:    {b['currency']} {b['checking']:.2f}")
        print(f"  Savings:     {b['currency']} {b['savings']:.2f}")
        print(f"  Investments: {b['currency']} {b['investments']:.2f}")
        print(f"  IBAN:        {b.get('iban', 'N/A')}")

    elif cmd == "txns":
        txns = get_bunq_payments(limit=10)
        print(f"\n  Last {len(txns)} bunq transactions:")
        for t in txns:
            print(f"  {t['created'][:10]}  {t['currency']} {float(t['amount']):>10.2f}  {t['counterparty']}")

    elif cmd == "topup":
        result = topup("500.00")
        print(json.dumps(result, indent=2, ensure_ascii=False))

    else:
        print("Usage: python bunq_bridge.py [balance|txns|topup]")