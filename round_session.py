"""
round_session.py — In-memory + JSON session state for a bill-splitting session.

Each session tracks:
  - The scanned items from the receipt
  - Each person's name, items, amount owed, bunq contact, payment status
  - The running pot and target total
  - The payment request IDs for tracking
"""

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

SESSIONS_DIR = Path(__file__).parent / "sessions"
SESSIONS_DIR.mkdir(exist_ok=True)

# In-memory cache — avoids hitting disk on every poll
_cache: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Session schema
# ---------------------------------------------------------------------------

def new_session(
    restaurant_name: str,
    items: list[dict],   # [{"name": str, "price": float}]
    total: float,
    currency: str = "EUR",
    restaurant_email: str = "sugardaddy@bunq.com",
) -> dict:
    session = {
        "id":               str(uuid.uuid4())[:8],
        "created":          datetime.now().isoformat(),
        "restaurant_name":  restaurant_name,
        "restaurant_email": restaurant_email,
        "currency":         currency,
        "items":            items,       # full item list from OCR
        "total":            total,
        "pot":              0.0,
        "people":           [],          # filled as people are added
        "status":           "assigning", # assigning → collecting → complete
        "released":         False,
    }
    _save(session)
    return session


def add_person(session: dict, name: str, items: list[dict], amount: float, contact: str) -> dict:
    """
    Add a person to the session.
    contact = bunq email or phone number of the person being charged.
    items = [{"name": str, "price": float}] — what they had
    """
    person = {
        "id":         str(uuid.uuid4())[:6],
        "name":       name,
        "items":      items,
        "amount":     round(amount, 2),
        "contact":    contact,
        "paid":       False,
        "request_id": None,   # bunq request-inquiry ID
        "bunqme_url": None,   # shareable payment link
        "paid_at":    None,
    }
    session["people"].append(person)
    _save(session)
    return person


def mark_paid(session: dict, person_id: str) -> bool:
    for p in session["people"]:
        if p["id"] == person_id:
            p["paid"] = True
            p["paid_at"] = datetime.now().isoformat()
            session["pot"] = round(sum(
                px["amount"] for px in session["people"] if px["paid"]
            ), 2)
            _save(session)
            return True
    return False


def all_paid(session: dict) -> bool:
    return (
        len(session["people"]) > 0
        and all(p["paid"] for p in session["people"])
    )


def get_session(session_id: str) -> Optional[dict]:
    if session_id in _cache:
        return _cache[session_id]
    path = SESSIONS_DIR / f"{session_id}.json"
    if path.exists():
        s = json.loads(path.read_text())
        _cache[session_id] = s
        return s
    return None


def _save(session: dict):
    _cache[session["id"]] = session
    path = SESSIONS_DIR / f"{session['id']}.json"
    path.write_text(json.dumps(session, indent=2, ensure_ascii=False))


def list_sessions() -> list[dict]:
    sessions = []
    for p in SESSIONS_DIR.glob("*.json"):
        try:
            sessions.append(json.loads(p.read_text()))
        except Exception:
            pass
    return sorted(sessions, key=lambda s: s["created"], reverse=True)