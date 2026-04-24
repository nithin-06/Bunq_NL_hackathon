"""
bunq_client.py — bunq Sandbox API wrapper.

Implements the full 4-step auth flow required by bunq:
  1. RSA key pair generation
  2. POST /installation  → installation token + server public key
  3. POST /device-server → device registration
  4. POST /session-server → session token + user ID

Then exposes:
  - get_accounts()         → list monetary accounts + balances
  - get_payments()         → recent transactions
  - make_payment()         → transfer money (requires signed request)
  - request_money()        → request money from sugardaddy (sandbox only)

State is persisted to bunq_state.json so you don't re-register on every run.

Install deps:
    pip install cryptography requests

Usage:
    python bunq_client.py setup          # Run once to register + start session
    python bunq_client.py balance        # Print account balances
    python bunq_client.py pay            # Make a test payment
    python bunq_client.py topup          # Request €500 from sugardaddy
"""

import base64
import json
import os
import sys
import uuid
from pathlib import Path

import requests
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from cryptography.x509 import load_pem_x509_certificate

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SANDBOX_BASE = "https://public-api.sandbox.bunq.com/v1"
STATE_FILE   = Path(__file__).parent / "bunq_state.json"
PRIVATE_KEY_FILE = Path(__file__).parent / "bunq_private_key.pem"
PUBLIC_KEY_FILE  = Path(__file__).parent / "bunq_public_key.pem"

# ⚠️  Paste your sandbox API key here, or set env var BUNQ_API_KEY
# Get it with: curl -X POST https://public-api.sandbox.bunq.com/v1/sandbox-user-person
API_KEY = os.environ.get("BUNQ_API_KEY", "sandbox_14f9b74b200df4e486d8b372f331c0ce65113246757e013e662b4032")


# ---------------------------------------------------------------------------
# RSA key management
# ---------------------------------------------------------------------------

def _generate_or_load_keys() -> tuple[str, str]:
    """Generate RSA 2048-bit key pair (or load existing). Returns (private_pem, public_pem)."""
    if PRIVATE_KEY_FILE.exists() and PUBLIC_KEY_FILE.exists():
        private_pem = PRIVATE_KEY_FILE.read_text()
        public_pem  = PUBLIC_KEY_FILE.read_text()
        print("[BUNQ] Using existing RSA key pair.")
        return private_pem, public_pem

    print("[BUNQ] Generating new RSA 2048-bit key pair...")
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend()
    )
    public_key = private_key.public_key()

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    ).decode("utf-8")

    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode("utf-8")

    PRIVATE_KEY_FILE.write_text(private_pem)
    PUBLIC_KEY_FILE.write_text(public_pem)
    print(f"[BUNQ] Keys saved to {PRIVATE_KEY_FILE} and {PUBLIC_KEY_FILE}")
    print("[BUNQ] ⚠️  Do NOT commit these files to git!")
    return private_pem, public_pem


def _sign(payload: str, private_pem: str) -> str:
    """Sign payload string with RSA-SHA256 PKCS1v15. Returns base64 signature."""
    private_key = load_pem_private_key(private_pem.encode(), password=None)
    signature = private_key.sign(
        payload.encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA256()
    )
    return base64.b64encode(signature).decode("utf-8")


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def _load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}

def _save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ---------------------------------------------------------------------------
# Standard headers (all requests need these)
# ---------------------------------------------------------------------------

def _base_headers(auth_token: str = None, signature: str = None) -> dict:
    h = {
        "Content-Type":       "application/json",
        "Cache-Control":      "no-cache",
        "User-Agent":         "BunqHackathonClient/1.0",
        "X-Bunq-Language":    "en_US",
        "X-Bunq-Region":      "nl_NL",
        "X-Bunq-Geolocation": "0 0 0 0 000",
        "X-Bunq-Client-Request-Id": str(uuid.uuid4()),
    }
    if auth_token:
        h["X-Bunq-Client-Authentication"] = auth_token
    if signature:
        h["X-Bunq-Client-Signature"] = signature
    return h


# ---------------------------------------------------------------------------
# Auth flow — 3 steps
# ---------------------------------------------------------------------------

def setup_installation(private_pem: str, public_pem: str, state: dict) -> dict:
    """POST /installation — sends our public key, gets installation token."""
    if state.get("installation_token"):
        print("[BUNQ] Installation already registered.")
        return state

    print("[BUNQ] Step 1/3: Creating installation...")
    payload = json.dumps({"client_public_key": public_pem})
    resp = requests.post(
        f"{SANDBOX_BASE}/installation",
        headers=_base_headers(),
        data=payload
    )
    resp.raise_for_status()
    data = resp.json()["Response"]

    state["installation_token"] = next(
        item["Token"]["token"] for item in data if "Token" in item
    )
    state["server_public_key"] = next(
        item["ServerPublicKey"]["server_public_key"] for item in data if "ServerPublicKey" in item
    )
    _save_state(state)
    print(f"[BUNQ] ✅ Installation token: {state['installation_token'][:20]}...")
    return state


def setup_device_server(private_pem: str, state: dict) -> dict:
    """POST /device-server — registers this machine with the API key."""
    if state.get("device_registered"):
        print("[BUNQ] Device already registered.")
        return state

    print("[BUNQ] Step 2/3: Registering device server...")
    payload = json.dumps({
        "description": "HackathonClient",
        "secret": API_KEY,
        "permitted_ips": ["*"]   # wildcard — any IP allowed (good for hackathon)
    })
    sig = _sign(payload, private_pem)

    resp = requests.post(
        f"{SANDBOX_BASE}/device-server",
        headers=_base_headers(auth_token=state["installation_token"], signature=sig),
        data=payload
    )
    resp.raise_for_status()
    state["device_registered"] = True
    _save_state(state)
    print("[BUNQ] ✅ Device registered.")
    return state


def create_session(private_pem: str, state: dict) -> dict:
    """POST /session-server — opens a session, gets session token + user ID."""
    print("[BUNQ] Step 3/3: Creating session...")
    payload = json.dumps({"secret": API_KEY}, separators=(",", ":"))
    sig = _sign(payload, private_pem)

    resp = requests.post(
        f"{SANDBOX_BASE}/session-server",
        headers=_base_headers(auth_token=state["installation_token"], signature=sig),
        data=payload
    )
    resp.raise_for_status()
    data = resp.json()["Response"]

    state["session_token"] = next(
        item["Token"]["token"] for item in data if "Token" in item
    )

    # User could be UserPerson or UserCompany
    for item in data:
        if "UserPerson" in item:
            state["user_id"] = item["UserPerson"]["id"]
            state["user_name"] = item["UserPerson"].get("display_name", "")
            break
        if "UserCompany" in item:
            state["user_id"] = item["UserCompany"]["id"]
            state["user_name"] = item["UserCompany"].get("display_name", "")
            break

    _save_state(state)
    print(f"[BUNQ] ✅ Session opened. User ID: {state['user_id']}  Name: {state.get('user_name','')}")
    print(f"[BUNQ]    Session token: {state['session_token'][:20]}...")
    return state


def full_setup() -> tuple[dict, str]:
    """Run the complete auth setup. Returns (state, private_pem)."""
    private_pem, public_pem = _generate_or_load_keys()
    state = _load_state()

    state = setup_installation(private_pem, public_pem, state)
    state = setup_device_server(private_pem, state)
    state = create_session(private_pem, state)
    return state, private_pem


# ---------------------------------------------------------------------------
# API calls
# ---------------------------------------------------------------------------

def get_accounts(state: dict) -> list[dict]:
    """GET /user/{id}/monetary-account — list all accounts with balances."""
    resp = requests.get(
        f"{SANDBOX_BASE}/user/{state['user_id']}/monetary-account",
        headers=_base_headers(auth_token=state["session_token"])
    )
    resp.raise_for_status()
    accounts = []
    for item in resp.json()["Response"]:
        for key in ("MonetaryAccountBank", "MonetaryAccount"):
            if key in item:
                acc = item[key]
                accounts.append({
                    "id":       acc["id"],
                    "iban":     next((a["value"] for a in acc.get("alias", []) if a["type"] == "IBAN"), None),
                    "email":    next((a["value"] for a in acc.get("alias", []) if a["type"] == "EMAIL"), None),
                    "balance":  acc.get("balance", {}).get("value"),
                    "currency": acc.get("currency", "EUR"),
                    "name":     acc.get("display_name", ""),
                })
    return accounts


def get_payments(state: dict, account_id: int, limit: int = 10) -> list[dict]:
    """GET /user/{id}/monetary-account/{acc_id}/payment — recent transactions."""
    resp = requests.get(
        f"{SANDBOX_BASE}/user/{state['user_id']}/monetary-account/{account_id}/payment",
        headers=_base_headers(auth_token=state["session_token"]),
        params={"count": limit}
    )
    resp.raise_for_status()
    payments = []
    for item in resp.json().get("Response", []):
        if "Payment" in item:
            p = item["Payment"]
            payments.append({
                "id":          p["id"],
                "created":     p["created"],
                "amount":      p["amount"]["value"],
                "currency":    p["amount"]["currency"],
                "description": p.get("description", ""),
                "type":        p.get("type", ""),
                "counterparty": p.get("counterparty_alias", {}).get("name", ""),
            })
    return payments


def make_payment(
    state: dict,
    private_pem: str,
    account_id: int,
    amount: str,
    description: str,
    recipient_email: str = "sugardaddy@bunq.com",
    recipient_name: str = "Sugar Daddy",
    currency: str = "EUR",
) -> dict:
    """
    POST /user/{id}/monetary-account/{acc_id}/payment
    Requires RSA-signed payload.
    """
    url = f"{SANDBOX_BASE}/user/{state['user_id']}/monetary-account/{account_id}/payment"

    payload = json.dumps({
        "amount": {"value": str(amount), "currency": currency},
        "counterparty_alias": {
            "type": "EMAIL",
            "value": recipient_email,
            "name": recipient_name
        },
        "description": str(description)
    }, separators=(",", ":"))

    sig = _sign(payload, private_pem)
    headers = _base_headers(auth_token=state["session_token"], signature=sig)

    resp = requests.post(url, headers=headers, data=payload)
    if not resp.ok:
        print(f"[BUNQ] Payment failed: {resp.status_code} — {resp.text}")
        resp.raise_for_status()

    print(f"[BUNQ] ✅ Payment of {currency} {amount} sent to {recipient_email}")
    return resp.json()


def request_money_from_sugardaddy(state: dict, private_pem: str, account_id: int, amount: str = "500.00") -> dict:
    """
    Request fake money from bunq's sugardaddy account (sandbox only).
    POST /user/{id}/monetary-account/{acc_id}/request-inquiry
    Max €500 per request.
    """
    url = f"{SANDBOX_BASE}/user/{state['user_id']}/monetary-account/{account_id}/request-inquiry"

    payload = json.dumps({
        "amount_inquired": {"value": str(amount), "currency": "EUR"},
        "counterparty_alias": {
            "type": "EMAIL",
            "value": "sugardaddy@bunq.com",
            "name": "Sugar Daddy"
        },
        "description": "Top up sandbox account",
        "allow_bunqme": False
    }, separators=(",", ":"))

    sig = _sign(payload, private_pem)
    resp = requests.post(
        url,
        headers=_base_headers(auth_token=state["session_token"], signature=sig),
        data=payload
    )
    if not resp.ok:
        print(f"[BUNQ] Topup failed: {resp.status_code} — {resp.text}")
        resp.raise_for_status()

    print(f"[BUNQ] ✅ Requested €{amount} from sugardaddy. Check your sandbox app!")
    return resp.json()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _require_state():
    state = _load_state()
    if not state.get("session_token"):
        print("[ERROR] No session found. Run: python bunq_client.py setup")
        sys.exit(1)
    private_pem, _ = _generate_or_load_keys()
    return state, private_pem


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"

    if cmd == "setup":
        # Run full 4-step auth — do this once
        state, private_pem = full_setup()
        print("\n[BUNQ] Setup complete. You can now run other commands.")

    elif cmd == "balance":
        state, private_pem = _require_state()
        accounts = get_accounts(state)
        print("\n=== ACCOUNTS ===")
        for acc in accounts:
            print(f"  ID: {acc['id']}  IBAN: {acc['iban']}  Balance: {acc['currency']} {acc['balance']}  ({acc['name']})")

    elif cmd == "payments":
        state, private_pem = _require_state()
        accounts = get_accounts(state)
        if not accounts:
            print("No accounts found.")
            sys.exit(1)
        acc_id = accounts[0]["id"]
        payments = get_payments(state, acc_id)
        print(f"\n=== LAST {len(payments)} PAYMENTS ===")
        for p in payments:
            print(f"  {p['created'][:10]}  {p['currency']} {p['amount']:>10}  {p['counterparty']:<25}  {p['description']}")

    elif cmd == "topup":
        state, private_pem = _require_state()
        accounts = get_accounts(state)
        acc_id = accounts[0]["id"]
        result = request_money_from_sugardaddy(state, private_pem, acc_id, amount="500.00")
        print(json.dumps(result, indent=2))

    elif cmd == "pay":
        # Test payment: send €0.10 to sugardaddy
        state, private_pem = _require_state()
        accounts = get_accounts(state)
        acc_id = accounts[0]["id"]
        result = make_payment(state, private_pem, acc_id, "0.10", "Test payment from hackathon app")
        print(json.dumps(result, indent=2))

    elif cmd == "session":
        # Refresh session (call this if you get 401 errors)
        private_pem, _ = _generate_or_load_keys()
        state = _load_state()
        state = create_session(private_pem, state)
        print("[BUNQ] Session refreshed.")

    else:
        print(__doc__)