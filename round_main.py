"""
round_main.py — Round bill splitter — full CLI orchestrator.

Usage:
  python round_main.py demo                      # Full demo with mock data
  python round_main.py scan <image>              # Scan a real bill image
  python round_main.py run <image>               # Full run with real image + mic
  python round_main.py status <session_id>       # Show session state
  python round_main.py pay <session_id>          # Force-release payment to restaurant
  python round_main.py topup                     # Top up sandbox account

Modes explained:
  demo   → mock bill + pre-scripted voice → demo payments → simulated pot fill
  scan   → real OCR on image, then text-mode assignment + real bunq payments
  run    → real OCR + real microphone + real bunq payments
"""

import sys
import time
import json
from pathlib import Path

import round_session as rs
import round_ocr
import round_voice
import round_bunq


# ---------------------------------------------------------------------------
# ANSI colours for terminal output
# ---------------------------------------------------------------------------

GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
BLUE   = "\033[94m"
BOLD   = "\033[1m"
RESET  = "\033[0m"
CYAN   = "\033[96m"


def _banner():
    print(f"""
{BOLD}{CYAN}
 ██████╗  ██████╗ ██╗   ██╗███╗   ██╗██████╗
 ██╔══██╗██╔═══██╗██║   ██║████╗  ██║██╔══██╗
 ██████╔╝██║   ██║██║   ██║██╔██╗ ██║██║  ██║
 ██╔══██╗██║   ██║██║   ██║██║╚██╗██║██║  ██║
 ██║  ██║╚██████╔╝╚██████╔╝██║ ╚████║██████╔╝
 ╚═╝  ╚═╝ ╚═════╝  ╚═════╝ ╚═╝  ╚═══╝╚═════╝
{RESET}
 {BOLD}Smart bill splitting powered by bunq{RESET}
 ─────────────────────────────────────────────
""")


# ---------------------------------------------------------------------------
# Demo mode — full scripted flow for judges
# ---------------------------------------------------------------------------

DEMO_SCRIPTS = [
    "Hi I'm Nithin, I had the Margherita Pizza and the House Red Wine",
    "I'm Apurva, I ordered the Caesar Salad and the Espresso x2",
    "I'm Bala, I had the Pasta Carbonara, the Tiramisu, and the Sparkling Water",
]


def run_demo():
    _banner()
    print(f"{BOLD}[ROUND] Starting DEMO mode — no real payments, fully scripted{RESET}\n")
    time.sleep(0.5)

    # Step 1: Scan (mock bill)
    print(f"{BLUE}━━━ STEP 1: SCAN BILL ━━━{RESET}")
    bill = round_ocr.mock_bill("La Bella Italia")
    round_ocr.display_bill(bill)
    time.sleep(1)

    # Step 2: Create session
    session = rs.new_session(
        items=bill["items"],
        total=bill["total"],
        restaurant_name=bill["restaurant"],
        restaurant_email="sugardaddy@bunq.com",
    )
    print(f"{GREEN}[SCAN] ✅ Session created: {session.session_id}{RESET}\n")
    time.sleep(0.5)

    # Step 3: Voice assignment
    print(f"{BLUE}━━━ STEP 2: VOICE ASSIGNMENT ━━━{RESET}")
    assignments = round_voice.assign_via_voice(
        bill_items=bill["items"],
        num_people=len(DEMO_SCRIPTS),
        use_mic=False,
        demo_scripts=DEMO_SCRIPTS,
    )
    time.sleep(0.5)

    # Add people to session
    for a in assignments:
        rs.add_person(session, a["name"], a["email"], a["items"])

    # Reload session (add_person saves to disk)
    session = rs.load_session(session.session_id)
    session.status = "collecting"

    # Handle unclaimed items — split among all
    unclaimed = rs.unclaimed_total(session)
    if unclaimed > 0 and session.people:
        split = round(unclaimed / len(session.people), 2)
        print(f"{YELLOW}[ASSIGN] €{unclaimed:.2f} unclaimed → split equally (€{split:.2f} each){RESET}")
        for p in session.people:
            p["amount_owed"] = round(p["amount_owed"] + split, 2)
        rs.save_session(session)

    print(f"\n{rs.summary(session)}")

    # Step 4: Send payment requests (demo — fake URLs)
    print(f"{BLUE}━━━ STEP 3: SEND PAYMENT REQUESTS ━━━{RESET}\n")
    for p in session.people:
        print(f"  📤 Sending request to {p['name']} ({p['email']}) — €{p['amount_owed']:.2f}")
        result = round_bunq.send_payment_request(
            person_name=p["name"],
            person_email=p["email"],
            amount=p["amount_owed"],
            description=f"Round at {bill['restaurant']}",
        )
        if result.get("bunqme_url"):
            print(f"     🔗 {result['bunqme_url']}")
        rs.set_payment_request(session, p["name"], result.get("bunqme_url", ""), result["request_id"])
        time.sleep(0.3)

    # Reload after updates
    session = rs.load_session(session.session_id)

    # Step 5: Simulate pot filling (demo mode)
    print(f"\n{BLUE}━━━ STEP 4: COLLECTING PAYMENTS ━━━{RESET}\n")
    all_paid = round_bunq.poll_until_complete(
        session=session,
        interval=2,
        timeout=60,
        demo_mode=True,
    )

    # Reload final state
    session = rs.load_session(session.session_id)

    # Step 6: Release
    if all_paid:
        print(f"\n{BLUE}━━━ STEP 5: PAY RESTAURANT ━━━{RESET}\n")
        print(f"  💳 Releasing €{session.total:.2f} to {session.restaurant_name}...")
        time.sleep(1)
        success = round_bunq.pay_restaurant(
            amount=session.total,
            restaurant_email=session.restaurant_email,
            restaurant_name=session.restaurant_name,
            description=f"Round — {session.restaurant_name} — table settled",
        )
        if success:
            session.status = "complete"
            rs.save_session(session)
            print(f"\n{GREEN}{BOLD}  🎉 Table settled! Everyone's square. Enjoy your night.{RESET}\n")
        else:
            print(f"{RED}  ⚠️  Payment failed — check bunq connection{RESET}")
    else:
        print(f"\n{YELLOW}  ⏱️  Some payments are still pending.{RESET}")
        print(f"  Session ID: {session.session_id} — run `python round_main.py status {session.session_id}` to check later.")

    print(rs.summary(session))


# ---------------------------------------------------------------------------
# Real run — scan + mic + real payments
# ---------------------------------------------------------------------------

def run_real(image_path: str, use_mic: bool = True):
    _banner()
    mode = "MIC" if use_mic else "TEXT"
    print(f"{BOLD}[ROUND] Starting LIVE mode ({mode}) — real bunq payments{RESET}\n")

    # Step 1: Scan
    print(f"{BLUE}━━━ STEP 1: SCAN BILL ━━━{RESET}")
    bill = round_ocr.scan_bill(image_path, verbose=True)
    round_ocr.display_bill(bill)

    # Confirm with user
    confirmed = input("  Confirm items are correct? (y/n): ").strip().lower()
    if confirmed != "y":
        print("  Aborting. Please try with a clearer image.")
        return

    # Step 2: Create session
    restaurant_email = input("  Restaurant bunq email (or Enter for sandbox): ").strip()
    if not restaurant_email:
        restaurant_email = "sugardaddy@bunq.com"

    session = rs.new_session(
        items=bill["items"],
        total=bill["total"],
        restaurant_name=bill["restaurant"],
        restaurant_email=restaurant_email,
    )
    print(f"{GREEN}[SCAN] ✅ Session: {session.session_id}{RESET}\n")

    # Step 3: Assign
    print(f"{BLUE}━━━ STEP 2: VOICE ASSIGNMENT ━━━{RESET}")
    num_people = int(input("  How many people? "))

    if use_mic:
        assignments = round_voice.assign_via_voice(
            bill_items=bill["items"],
            num_people=num_people,
            use_mic=True,
        )
    else:
        assignments = round_voice.assign_via_text(bill_items=bill["items"])

    for a in assignments:
        rs.add_person(session, a["name"], a["email"], a["items"])

    session = rs.load_session(session.session_id)

    # Handle unclaimed
    unclaimed = rs.unclaimed_total(session)
    if unclaimed > 0:
        print(f"{YELLOW}  €{unclaimed:.2f} unclaimed — splitting equally{RESET}")
        split = round(unclaimed / len(session.people), 2) if session.people else 0
        for p in session.people:
            p["amount_owed"] = round(p["amount_owed"] + split, 2)
        rs.save_session(session)

    session.status = "collecting"
    rs.save_session(session)
    print(rs.summary(session))

    # Step 4: Send requests
    print(f"{BLUE}━━━ STEP 3: SEND PAYMENT REQUESTS ━━━{RESET}\n")
    for p in session.people:
        result = round_bunq.send_payment_request(
            person_name=p["name"],
            person_email=p["email"],
            amount=p["amount_owed"],
            description=f"Round at {bill['restaurant']}",
        )
        if result.get("bunqme_url"):
            print(f"  🔗 {p['name']}: {result['bunqme_url']}")
        rs.set_payment_request(session, p["name"], result.get("bunqme_url", ""), result["request_id"])

    session = rs.load_session(session.session_id)

    # Step 5: Poll
    print(f"\n{BLUE}━━━ STEP 4: COLLECTING PAYMENTS ━━━{RESET}\n")
    all_paid = round_bunq.poll_until_complete(session=session, interval=5, timeout=300, demo_mode=False)
    session = rs.load_session(session.session_id)

    # Step 6: Release
    if all_paid:
        print(f"\n{BLUE}━━━ STEP 5: PAY RESTAURANT ━━━{RESET}")
        round_bunq.pay_restaurant(
            amount=session.total,
            restaurant_email=session.restaurant_email,
            restaurant_name=session.restaurant_name,
        )
        session.status = "complete"
        rs.save_session(session)
        print(f"\n{GREEN}{BOLD}  🎉 Table settled!{RESET}")


# ---------------------------------------------------------------------------
# CLI sub-commands
# ---------------------------------------------------------------------------

def cmd_status(session_id: str):
    try:
        session = rs.load_session(session_id)
        print(rs.summary(session))
        print("People:")
        for p in session.people:
            print(f"  {'✅' if p['paid'] else '⏳'} {p['name']} — €{p['amount_owed']:.2f} — {p.get('payment_url', 'no link')}")
    except FileNotFoundError:
        print(f"[ERROR] Session {session_id} not found.")


def cmd_pay(session_id: str):
    try:
        session = rs.load_session(session_id)
        print(f"[RELEASE] Paying restaurant €{session.total:.2f}...")
        round_bunq.pay_restaurant(session.total, session.restaurant_email, session.restaurant_name)
        session.status = "complete"
        rs.save_session(session)
    except FileNotFoundError:
        print(f"[ERROR] Session {session_id} not found.")


def cmd_topup():
    _ensure_bunq()
    print("[TOPUP] Requesting €500 from sugardaddy...")
    import bunq_client as bc
    state = bc._load_state()
    private_pem, _ = bc._generate_or_load_keys()
    accounts = bc.get_accounts(state)
    if not accounts:
        print("[ERROR] No accounts. Run: python bunq_client.py setup")
        return
    acc_id = accounts[0]["id"]
    bc.request_money_from_sugardaddy(state, private_pem, acc_id)


def _ensure_bunq():
    sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    _ensure_bunq()

    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1].lower()

    if cmd == "demo":
        run_demo()

    elif cmd == "scan" and len(sys.argv) >= 3:
        bill = round_ocr.scan_bill(sys.argv[2], verbose=True)
        round_ocr.display_bill(bill)

    elif cmd == "run" and len(sys.argv) >= 3:
        use_mic = "--text" not in sys.argv
        run_real(sys.argv[2], use_mic=use_mic)

    elif cmd == "run" and len(sys.argv) < 3:
        # No image — use mock bill
        run_real.__doc__  # just to reference it
        print("[ROUND] No image provided, using mock bill...")
        bill = round_ocr.mock_bill()
        tmp_path = "/tmp/round_mock_bill.txt"
        with open(tmp_path, "w") as f:
            f.write("\n".join(f"{i['name']} {i['price']:.2f}" for i in bill["items"]))
        run_real(tmp_path, use_mic="--text" not in sys.argv)

    elif cmd == "status" and len(sys.argv) >= 3:
        cmd_status(sys.argv[2])

    elif cmd == "pay" and len(sys.argv) >= 3:
        cmd_pay(sys.argv[2])

    elif cmd == "topup":
        cmd_topup()

    elif cmd == "balance":
        bal = round_bunq.get_balance()
        print(f"\n  💰 Balance: {bal['currency']} {bal['checking']:.2f}")
        if bal.get("iban"):
            print(f"  🏦 IBAN: {bal['iban']}")

    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()