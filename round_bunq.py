"""
round_bunq.py — bunq API integration for Round.

Uses our existing bunq_client.py auth layer.
Adds:
  - create_payment_request()  → bunq request-inquiry + bunqme tab → shareable link
  - poll_payments()           → check who has paid
  - pay_restaurant()          → release full amount to restaurant
"""

import json
import sys
import uuid
from pathlib import Path

import requests

# Pull in the existing bunq auth layer
sys.path.insert(0, str(Path(__file__).parent.parent / "receipt_module"))
import bunq_client as bc

SANDBOX_BASE = "https://public-api.sandbox.bunq.com/v1"

# ---------------------------------------------------------------------------
# Session bootstrap
# ---------------------------------------------------------------------------

_state: dict = {}
_private_pem: str = ""
_account_id: int = 0
_initialized: bool = False


def _is_real_payment_url(url: str) -> bool:
    if not url or not isinstance(url, str):
        return False
    lowered = url.lower().strip()
    if not lowered.startswith("http://") and not lowered.startswith("https://"):
        return False
    if lowered in {"https://bunq.me/round", "https://bunq.me/round/"}:
        return False
    if lowered.startswith("https://bunq.me/round/"):
        return False
    if "demo_" in lowered:
        return False
    return True


def _ensure_ready():
    global _state, _private_pem, _account_id, _initialized
    if _initialized:
        return

    _state = bc._load_state()
    _private_pem, _ = bc._generate_or_load_keys()

    if not _state.get("session_token"):
        print("[ROUND-BUNQ] No session — running setup...")
        _state, _private_pem = bc.full_setup()

    if _state.get("primary_account_id"):
        _account_id = int(_state["primary_account_id"])
    else:
        accounts = bc.get_accounts(_state)
        if not accounts:
            raise RuntimeError("No bunq accounts found. Run: python bunq_client.py topup")
        _account_id = accounts[0]["id"]
        _state["primary_account_id"] = _account_id
        bc._save_state(_state)

    _initialized = True
    print(f"[ROUND-BUNQ] Ready. Account: {_account_id}")


def _refresh():
    global _state, _initialized
    _state = bc.create_session(_private_pem, _state)
    _initialized = True


def _headers(sig: str = None) -> dict:
    h = {
        "Content-Type":                  "application/json",
        "Cache-Control":                 "no-cache",
        "User-Agent":                    "RoundBillSplitter/1.0",
        "X-Bunq-Language":               "en_US",
        "X-Bunq-Region":                 "nl_NL",
        "X-Bunq-Geolocation":            "0 0 0 0 000",
        "X-Bunq-Client-Request-Id":      str(uuid.uuid4()),
        "X-Bunq-Client-Authentication":  _state["session_token"],
    }
    if sig:
        h["X-Bunq-Client-Signature"] = sig
    return h


# ---------------------------------------------------------------------------
# Create a bunqme tab (shareable payment link with QR)
# ---------------------------------------------------------------------------

def create_bunqme_tab(amount: float, description: str) -> dict:
    """
    Create a bunq.me payment link anyone can pay without a bunq account.
    Returns {"tab_id": int, "url": str}
    """
    _ensure_ready()

    payload = json.dumps({
        "bunqme_tab_entry": {
            "amount_inquired": {
                "value":    f"{amount:.2f}",
                "currency": "EUR",
            },
            "description":   description,
            "redirect_url":  "https://bunq.com",
        }
    }, separators=(",", ":"))

    sig = bc._sign(payload, _private_pem)
    url = f"{SANDBOX_BASE}/user/{_state['user_id']}/monetary-account/{_account_id}/bunqme-tab"

    resp = requests.post(url, headers=_headers(sig), data=payload)

    if resp.status_code == 401:
        _refresh()
        sig = bc._sign(payload, _private_pem)
        resp = requests.post(url, headers=_headers(sig), data=payload)

    if not resp.ok:
        print(f"[ROUND-BUNQ] bunqme-tab failed {resp.status_code}: {resp.text}")
        # Fallback: return a simulated URL for demo purposes
        tab_id = f"demo_{uuid.uuid4().hex[:8]}"
        return {
            "tab_id":  tab_id,
            "url":     f"https://bunq.me/Round/{amount:.2f}/EUR/{tab_id}",
            "simulated": True,
        }

    data = resp.json().get("Response", [{}])
    tab_id = next((item["Id"]["id"] for item in data if "Id" in item), None)

    # Fetch the tab to get the share URL
    tab_url = f"{SANDBOX_BASE}/user/{_state['user_id']}/monetary-account/{_account_id}/bunqme-tab/{tab_id}"
    tab_resp = requests.get(tab_url, headers=_headers())
    share_url = None
    if tab_resp.ok:
        tab_data = tab_resp.json().get("Response", [{}])
        for item in tab_data:
            if "BunqMeTab" in item:
                share_url = item["BunqMeTab"].get("bunqme_tab_share_url")
                break

    if not _is_real_payment_url(share_url):
        print(f"[ROUND-BUNQ] bunqme-tab created but no valid share URL returned for tab {tab_id}")
        return {"tab_id": tab_id, "url": None, "simulated": False}

    return {"tab_id": tab_id, "url": share_url, "simulated": False}


# ---------------------------------------------------------------------------
# Create a request-inquiry (direct bunq-to-bunq payment request)
# ---------------------------------------------------------------------------

def create_payment_request(
    person_name: str,
    contact: str,
    amount: float,
    description: str,
) -> dict:
    """
    Send a payment request directly to a bunq user (email or phone).
    Returns {"request_id": int, "url": str, "method": "request_inquiry"|"bunqme"}

    Falls back to bunqme tab if the contact is not on bunq.
    """
    _ensure_ready()

    # Determine contact type
    contact_type = "EMAIL" if "@" in contact else "PHONE_NUMBER"

    payload = json.dumps({
        "amount_inquired": {
            "value":    f"{amount:.2f}",
            "currency": "EUR",
        },
        "counterparty_alias": {
            "type":  contact_type,
            "value": contact,
            "name":  person_name,
        },
        "description":  description,
        "allow_bunqme": True,
    }, separators=(",", ":"))

    sig = bc._sign(payload, _private_pem)
    url = f"{SANDBOX_BASE}/user/{_state['user_id']}/monetary-account/{_account_id}/request-inquiry"

    resp = requests.post(url, headers=_headers(sig), data=payload)

    if resp.status_code == 401:
        _refresh()
        sig = bc._sign(payload, _private_pem)
        resp = requests.post(url, headers=_headers(sig), data=payload)

    if not resp.ok:
        print(f"[ROUND-BUNQ] request-inquiry failed ({resp.status_code}), falling back to bunqme tab")
        tab = create_bunqme_tab(amount, description)
        return {
            "request_id": None,
            "url":        tab["url"],
            "method":     "bunqme",
            "simulated":  tab.get("simulated", False),
        }

    data = resp.json().get("Response", [{}])
    request_id = next((item["Id"]["id"] for item in data if "Id" in item), None)

    # Prefer a dedicated bunq.me tab for sharing so the public payment flow
    # lands on the bunq checkout page instead of request triage.
    share_tab = create_bunqme_tab(amount, description)
    if _is_real_payment_url(share_tab.get("url")):
        print(f"[ROUND-BUNQ] Payment request sent to {contact} and bunq.me tab created for sharing")
        return {
            "request_id": request_id,
            "url":        share_tab["url"],
            "method":     "request_inquiry+bunqme",
            "simulated":  share_tab.get("simulated", False),
        }

    # Fall back to any share URL bundled with the request-inquiry response.
    bunqme_url = None
    for item in data:
        if "RequestInquiry" in item:
            ri = item["RequestInquiry"]
            if ri.get("bunqme_share_url"):
                bunqme_url = ri["bunqme_share_url"]
            break

    if not _is_real_payment_url(bunqme_url):
        print("[ROUND-BUNQ] request-inquiry had no valid bunq.me share URL, creating bunqme tab for sharing")
        tab = create_bunqme_tab(amount, description)
        return {
            "request_id": request_id,
            "url":        tab["url"],
            "method":     "request_inquiry+bunqme",
            "simulated":  tab.get("simulated", False),
        }

    print(f"[ROUND-BUNQ] ✅ Payment request sent to {contact} for €{amount:.2f} — ID: {request_id}")
    return {
        "request_id": request_id,
        "url":        bunqme_url,
        "method":     "request_inquiry",
        "simulated":  False,
    }


# ---------------------------------------------------------------------------
# Poll who has paid
# ---------------------------------------------------------------------------

def get_pending_requests(request_ids: list[int]) -> dict[int, str]:
    """
    Check status of request-inquiry IDs.
    Returns {request_id: status} where status is PENDING|ACCEPTED|REJECTED|EXPIRED
    """
    _ensure_ready()
    statuses = {}

    for rid in request_ids:
        if rid is None:
            continue
        url = f"{SANDBOX_BASE}/user/{_state['user_id']}/monetary-account/{_account_id}/request-inquiry/{rid}"
        resp = requests.get(url, headers=_headers())
        if resp.ok:
            data = resp.json().get("Response", [{}])
            for item in data:
                if "RequestInquiry" in item:
                    statuses[rid] = item["RequestInquiry"].get("status", "PENDING")
        else:
            statuses[rid] = "UNKNOWN"

    return statuses


def poll_session_payments(session: dict) -> list[str]:
    """
    Check all pending people in the session.
    Returns list of person IDs who just paid (newly detected).
    """
    _ensure_ready()
    newly_paid = []

    # Collect request IDs for pending people
    pending = [p for p in session["people"] if not p["paid"] and p.get("request_id")]
    if not pending:
        return []

    request_ids = [p["request_id"] for p in pending if p["request_id"]]
    statuses = get_pending_requests(request_ids)

    for person in pending:
        rid = person.get("request_id")
        if rid and statuses.get(rid) == "ACCEPTED":
            newly_paid.append(person["id"])

    return newly_paid


# ---------------------------------------------------------------------------
# Pay restaurant (release)
# ---------------------------------------------------------------------------

def pay_restaurant(amount: float, restaurant_email: str, restaurant_name: str, description: str) -> dict:
    """
    Send the collected amount to the restaurant.
    """
    _ensure_ready()
    return bc.make_payment(
        state=_state,
        private_pem=_private_pem,
        account_id=_account_id,
        amount=f"{amount:.2f}",
        description=description,
        recipient_email=restaurant_email,
        recipient_name=restaurant_name,
    )


# ---------------------------------------------------------------------------
# Get account info for display
# ---------------------------------------------------------------------------

def get_account_info() -> dict:
    _ensure_ready()
    accounts = bc.get_accounts(_state)
    if not accounts:
        return {}
    acc = accounts[0]
    return {
        "balance": float(acc.get("balance") or 0),
        "iban":    acc.get("iban", ""),
        "email":   acc.get("email", ""),
        "name":    acc.get("name", ""),
    }
