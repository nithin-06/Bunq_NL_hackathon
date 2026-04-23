"""
mock_bank.py — CSV-backed banking wrapper.

All state persists to ./data/ CSVs so:
  - transactions survive between runs
  - edit CSVs directly to set up test scenarios
  - dashboard reads real historical data

Files written:
  data/account.csv       — checking, savings balances
  data/portfolio.csv     — investment holdings
  data/transactions.csv  — full transaction log (append-only)
"""

import csv
import os
from datetime import datetime
from pathlib import Path

DATA_DIR         = Path(__file__).parent / "data"
ACCOUNT_CSV      = DATA_DIR / "account.csv"
PORTFOLIO_CSV    = DATA_DIR / "portfolio.csv"
TRANSACTIONS_CSV = DATA_DIR / "transactions.csv"

TX_FIELDS = [
    "timestamp", "type", "merchant", "category",
    "instrument", "amount", "units_purchased",
    "price_per_unit", "balance_after", "note",
]


# ---------------------------------------------------------------------------
# Init — seed CSVs if they don't exist
# ---------------------------------------------------------------------------

def _init():
    DATA_DIR.mkdir(exist_ok=True)

    if not ACCOUNT_CSV.exists():
        with open(ACCOUNT_CSV, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["key", "value"])
            w.writeheader()
            w.writerows([
                {"key": "checking", "value": 4250.00},
                {"key": "savings",  "value": 1800.00},
                {"key": "currency", "value": "EUR"},
            ])

    if not PORTFOLIO_CSV.exists():
        with open(PORTFOLIO_CSV, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["instrument", "units", "price_per_unit"])
            w.writeheader()
            w.writerows([
                {"instrument": "ETF_SP500", "units": 5.2,  "price_per_unit": 410.0},
                {"instrument": "ETF_BONDS", "units": 10.0, "price_per_unit": 95.0},
            ])

    if not TRANSACTIONS_CSV.exists():
        with open(TRANSACTIONS_CSV, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=TX_FIELDS)
            w.writeheader()

_init()


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

def _read_account() -> dict:
    with open(ACCOUNT_CSV) as f:
        return {row["key"]: row["value"] for row in csv.DictReader(f)}

def _write_account(data: dict):
    with open(ACCOUNT_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["key", "value"])
        w.writeheader()
        for k, v in data.items():
            w.writerow({"key": k, "value": v})

def _read_portfolio() -> dict:
    with open(PORTFOLIO_CSV) as f:
        return {
            row["instrument"]: {
                "units": float(row["units"]),
                "price_per_unit": float(row["price_per_unit"]),
            }
            for row in csv.DictReader(f)
        }

def _write_portfolio(portfolio: dict):
    with open(PORTFOLIO_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["instrument", "units", "price_per_unit"])
        w.writeheader()
        for instrument, d in portfolio.items():
            w.writerow({"instrument": instrument, "units": round(d["units"], 4), "price_per_unit": d["price_per_unit"]})

def _append_tx(tx: dict):
    with open(TRANSACTIONS_CSV, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=TX_FIELDS, extrasaction="ignore")
        w.writerow(tx)

def _read_tx(limit: int = None) -> list[dict]:
    with open(TRANSACTIONS_CSV) as f:
        rows = list(csv.DictReader(f))
    return rows[-limit:] if limit else rows


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_balance() -> dict:
    acc = _read_account()
    portfolio = _read_portfolio()
    portfolio_value = sum(v["units"] * v["price_per_unit"] for v in portfolio.values())
    return {
        "checking":    round(float(acc.get("checking", 0)), 2),
        "savings":     round(float(acc.get("savings",  0)), 2),
        "investments": round(portfolio_value, 2),
        "currency":    acc.get("currency", "EUR"),
    }

def get_portfolio() -> dict:
    return _read_portfolio()

def get_recent_transactions(limit: int = 5) -> list[dict]:
    return _read_tx(limit=limit)

def get_all_transactions() -> list[dict]:
    return _read_tx()


def invest(amount: float, instrument: str = "ETF_SP500") -> dict:
    acc = _read_account()
    checking = float(acc["checking"])

    if amount <= 0:
        return {"success": False, "reason": "Amount must be positive."}
    if amount > checking:
        return {"success": False, "reason": f"Insufficient funds. Balance: €{checking:.2f}, requested: €{amount:.2f}"}

    prices = {"ETF_SP500": 410.0, "ETF_BONDS": 95.0, "CRYPTO_BTC": 62_000.0}
    price = prices.get(instrument, 100.0)
    units = round(amount / price, 4)
    new_balance = round(checking - amount, 2)

    acc["checking"] = new_balance
    _write_account(acc)

    portfolio = _read_portfolio()
    if instrument not in portfolio:
        portfolio[instrument] = {"units": 0, "price_per_unit": price}
    portfolio[instrument]["units"] = round(portfolio[instrument]["units"] + units, 4)
    _write_portfolio(portfolio)

    tx = {
        "timestamp": datetime.now().isoformat(), "type": "investment",
        "merchant": "", "category": "investment", "instrument": instrument,
        "amount": amount, "units_purchased": units, "price_per_unit": price,
        "balance_after": new_balance, "note": f"Bought {units} units of {instrument}",
    }
    _append_tx(tx)
    print(f"[BANK] ✅ Invested €{amount:.2f} → {units} units of {instrument} @ €{price:.2f}/unit")
    return {"success": True, "transaction": tx}


def transfer_to_savings(amount: float) -> dict:
    acc = _read_account()
    checking = float(acc["checking"])
    savings  = float(acc["savings"])

    if amount > checking:
        return {"success": False, "reason": "Insufficient funds in checking."}

    new_checking = round(checking - amount, 2)
    new_savings  = round(savings + amount,  2)
    acc["checking"] = new_checking
    acc["savings"]  = new_savings
    _write_account(acc)

    tx = {
        "timestamp": datetime.now().isoformat(), "type": "savings_transfer",
        "merchant": "", "category": "savings", "instrument": "",
        "amount": amount, "units_purchased": "", "price_per_unit": "",
        "balance_after": new_checking, "note": f"Moved €{amount:.2f} to savings",
    }
    _append_tx(tx)
    print(f"[BANK] ✅ Transferred €{amount:.2f} to savings")
    return {"success": True, "transaction": tx}


def log_expense(merchant: str, amount: float, category: str) -> dict:
    acc = _read_account()
    checking = float(acc["checking"])
    new_balance = round(checking - amount, 2)
    acc["checking"] = new_balance
    _write_account(acc)

    tx = {
        "timestamp": datetime.now().isoformat(), "type": "expense",
        "merchant": merchant, "category": category, "instrument": "",
        "amount": amount, "units_purchased": "", "price_per_unit": "",
        "balance_after": new_balance, "note": f"{category} at {merchant}",
    }
    _append_tx(tx)
    print(f"[BANK] ✅ Logged €{amount:.2f} expense at {merchant} [{category}]")
    return {"success": True, "transaction": tx}


def reset_to_defaults():
    """Wipe CSVs and reinitialise with seed data. Use before a fresh demo."""
    for p in [ACCOUNT_CSV, PORTFOLIO_CSV, TRANSACTIONS_CSV]:
        if p.exists():
            p.unlink()
    _init()
    print("[BANK] 🔄 Database reset to defaults.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import json, sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "balance"
    if cmd == "balance":
        print(json.dumps(get_balance(), indent=2))
    elif cmd == "txns":
        for t in get_all_transactions(): print(t)
    elif cmd == "portfolio":
        print(json.dumps(get_portfolio(), indent=2))
    elif cmd == "reset":
        reset_to_defaults()