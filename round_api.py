"""
round_api.py — FastAPI backend for Round.

Endpoints:
  POST /scan                 Upload receipt image → items + session created
  POST /assign               Add a person (voice audio OR text) → matched items + amount
  POST /collect              Send bunq payment requests to all people
  GET  /session/{id}         Get session status (for live polling)
  POST /poll/{id}            Check bunq for new payments
  POST /release/{id}         Pay restaurant when all paid
  GET  /account              Get organiser bunq account info
  POST /session/{id}/reset   Clear a session (for demo resets)

Run:
    uvicorn round_api:app --reload --port 8000
"""

import json
import os
import re
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent))

import round_session as rs
import round_bunq as rb
import round_voice as rv
import round_ocr as ro

app = FastAPI(title="Round — Bill Splitter", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve the UI
UI_DIR = Path(__file__).parent / "ui"
if UI_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(UI_DIR)), name="static")


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class PersonText(BaseModel):
    session_id: str
    name: str
    contact: str       # bunq email or phone
    text: str          # what they said, typed


class CollectRequest(BaseModel):
    session_id: str


class ReleaseRequest(BaseModel):
    session_id: str
    restaurant_email: str = "sugardaddy@bunq.com"
    restaurant_name: str  = "Restaurant"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
async def root():
    index = UI_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return {"status": "Round API running", "docs": "/docs"}


@app.get("/account")
async def get_account():
    """Get organiser's bunq account info."""
    try:
        info = rb.get_account_info()
        return {"ok": True, "account": info}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/scan")
async def scan_receipt(image: UploadFile = File(...)):
    """
    Upload a receipt image.
    Returns the parsed items and creates a new session.
    """
    if image.content_type and not image.content_type.startswith("image/"):
        raise HTTPException(400, "Only image files accepted")

    content = await image.read()
    if not content:
        raise HTTPException(400, "Uploaded file is empty")

    suffix = Path(image.filename or "receipt.jpg").suffix.lower() or ".jpg"
    if suffix not in {".jpg", ".jpeg", ".png"}:
        suffix = ".jpg"

    try:
        parsed = ro.scan_receipt_bytes(content, suffix=suffix)
    except Exception as e:
        raise HTTPException(500, f"OCR failed: {e}")

    # Create session
    session = rs.new_session(
        restaurant_name=parsed["restaurant"],
        items=parsed["items"],
        total=parsed["total"],
        currency=parsed.get("currency", "EUR"),
    )

    return {
        "ok":         True,
        "session_id": session["id"],
        "restaurant": parsed["restaurant"],
        "items":      parsed["items"],
        "total":      parsed["total"],
        "currency":   parsed.get("currency", "EUR"),
        "discount":   parsed.get("discount"),
    }


@app.post("/assign/voice")
async def assign_voice(
    session_id: str = Form(...),
    name:       str = Form(...),
    contact:    str = Form(...),
    audio:      UploadFile = File(...),
):
    """
    Add a person via voice recording.
    Transcribes audio, matches items, returns their bill share.
    """
    session = rs.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    if session.get("status") != "assigning":
        raise HTTPException(400, "People can only be changed before payment collection starts")

    audio_bytes = await audio.read()

    # Transcribe
    try:
        transcript = rv.transcribe_bytes(audio_bytes)
    except Exception as e:
        raise HTTPException(500, f"Transcription failed: {e}")

    # Match items
    matched = rv.match_items(transcript, session["items"])
    if not matched["matched_items"]:
        raise HTTPException(400, "No receipt items matched for that person")
    assigned_items = _allocate_matched_items(session, matched["matched_items"])
    amount = round(sum(item["price"] for item in assigned_items), 2)

    # Add to session
    person = rs.add_person(session, name, assigned_items, amount, contact)
    person["source_mode"] = "voice"
    person["source_text"] = transcript
    person["transcript"] = transcript
    rs._save(session)

    return {
        "ok":         True,
        "person_id":  person["id"],
        "name":       name,
        "transcript": transcript,
        "items":      assigned_items,
        "amount":     amount,
        "confidence": matched.get("confidence", 0),
        "remaining":  _unassigned_total(session),
        "session":    session,
        "assignment": _assignment_summary(session),
    }


@app.post("/assign/text")
async def assign_text(body: PersonText):
    """
    Add a person via typed text.
    Matches text to items, returns their bill share.
    """
    session = rs.get_session(body.session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    if session.get("status") != "assigning":
        raise HTTPException(400, "People can only be changed before payment collection starts")

    session_items = session.get("items") or []
    print(f"[ASSIGN/TEXT] session_id={body.session_id} items_in_session={session_items}")
    print(f"[ASSIGN/TEXT] input text='{body.text}'")

    if not session_items:
        raise HTTPException(400, f"Session has no items — OCR may have failed. Session items: {session_items!r}")

    matched = rv.match_items(body.text, session_items)
    if not matched["matched_items"]:
        raise HTTPException(400, "No receipt items matched for that person")
    assigned_items = _allocate_matched_items(session, matched["matched_items"])
    amount = round(sum(item["price"] for item in assigned_items), 2)

    print(f"[ASSIGN/TEXT] matched={matched}")

    person = rs.add_person(session, body.name, assigned_items, amount, body.contact)
    person["source_mode"] = "text"
    person["source_text"] = body.text
    rs._save(session)

    return {
        "ok":        True,
        "person_id": person["id"],
        "name":      body.name,
        "items":     assigned_items,
        "amount":    amount,
        "confidence": matched.get("confidence", 0),
        "remaining": _unassigned_total(session),
        "debug_session_items": session_items,
        "session":   session,
        "assignment": _assignment_summary(session),
    }


@app.get("/session/{session_id}/items")
async def get_session_items(session_id: str):
    """Debug: see exactly what items are stored in the session."""
    session = rs.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    return {
        "ok": True,
        "session_id": session_id,
        "restaurant": session.get("restaurant_name"),
        "total": session.get("total"),
        "items": session.get("items") or [],
        "items_count": len(session.get("items") or []),
    }


@app.post("/collect")
async def collect_payments(body: CollectRequest, background_tasks: BackgroundTasks):
    """
    Send bunq payment requests to all people in the session.
    Switches session status to 'collecting'.
    """
    session = rs.get_session(body.session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    if not session["people"]:
        raise HTTPException(400, "No people added yet")
    if session.get("status") != "assigning":
        raise HTTPException(400, "Payment collection already started")

    remaining = _unassigned_total(session)
    if remaining > 0.01:
        raise HTTPException(400, f"Assign the remaining €{remaining:.2f} before sending payment requests")

    results = []
    for person in session["people"]:
        if person.get("bunqme_url"):
            # Already requested
            results.append({
                "person_id":  person["id"],
                "name":      person["name"],
                "amount":    person["amount"],
                "url":       person["bunqme_url"],
                "portal_url": _payment_portal_url(body.session_id, person["id"]),
                "status":    "already_sent",
                "method":    person.get("request_method"),
                "simulated": person.get("request_simulated", False),
            })
            continue

        desc = f"Round: {session['restaurant_name']} — {person['name']}'s share"
        try:
            req = rb.create_payment_request(
                person_name=person["name"],
                contact=person["contact"],
                amount=person["amount"],
                description=desc,
            )
            person["request_id"] = req.get("request_id")
            person["bunqme_url"] = req.get("url")
            person["request_method"] = req.get("method")
            person["request_simulated"] = req.get("simulated", False)
        except Exception as e:
            person["bunqme_url"] = f"https://bunq.me/round/{person['amount']:.2f}/error"
            person["request_method"] = "error"
            person["request_simulated"] = True
            print(f"[API] Payment request failed for {person['name']}: {e}")

        results.append({
            "person_id":  person["id"],
            "name":      person["name"],
            "amount":    person["amount"],
            "url":       person["bunqme_url"],
            "portal_url": _payment_portal_url(body.session_id, person["id"]),
            "status":    "sent",
            "method":    person.get("request_method"),
            "simulated": person.get("request_simulated", False),
        })

    session["status"] = "collecting"
    rs._save(session)

    # Start background polling
    background_tasks.add_task(_poll_loop, body.session_id)

    return {"ok": True, "requests": results}


@app.get("/session/{session_id}")
async def get_session(session_id: str):
    """Get full session status — called by frontend every few seconds."""
    session = rs.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    paid_count = sum(1 for p in session["people"] if p["paid"])
    total_count = len(session["people"])

    return {
        "ok":         True,
        "session":    session,
        "paid_count": paid_count,
        "total_count": total_count,
        "all_paid":   rs.all_paid(session),
        "pot":        session["pot"],
        "target":     session["total"],
        "pct":        round(session["pot"] / session["total"] * 100, 1) if session["total"] > 0 else 0,
        "assignment": _assignment_summary(session),
    }


@app.post("/poll/{session_id}")
async def poll_payments(session_id: str):
    """Manually trigger a payment poll (frontend can also call this)."""
    session = rs.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    newly_paid = rb.poll_session_payments(session)
    for pid in newly_paid:
        rs.mark_paid(session, pid)
        print(f"[API] ✅ {pid} marked as paid")

    rs._save(session)
    return {
        "ok":        True,
        "newly_paid": newly_paid,
        "all_paid":  rs.all_paid(session),
        "pot":       session["pot"],
    }


@app.post("/release/{session_id}")
async def release_payment(session_id: str, body: ReleaseRequest):
    """
    Pay the restaurant. Called when all people have paid (or manually).
    """
    session = rs.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    if session["released"]:
        return {"ok": True, "message": "Already released", "amount": session.get("total", 0)}
    if session.get("pot", 0) + 0.01 < session.get("total", 0):
        raise HTTPException(400, f"Cannot release yet: only €{session.get('pot', 0):.2f} of €{session.get('total', 0):.2f} has been paid")

    amount = session["total"]
    try:
        result = rb.pay_restaurant(
            amount=amount,
            restaurant_email=body.restaurant_email,
            restaurant_name=body.restaurant_name,
            description=f"Round: Full payment for {session['restaurant_name']} — {len(session['people'])} people",
        )
        session["released"] = True
        session["status"] = "complete"
        rs._save(session)
        return {"ok": True, "amount": amount, "result": result}
    except Exception as e:
        raise HTTPException(500, f"Release failed: {e}")


@app.post("/session/{session_id}/mark-paid/{person_id}")
async def manual_mark_paid(session_id: str, person_id: str):
    """Manually mark someone as paid (for demo purposes)."""
    session = rs.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    success = rs.mark_paid(session, person_id)
    return {"ok": success, "all_paid": rs.all_paid(session), "pot": session["pot"]}


@app.delete("/session/{session_id}/person/{person_id}")
async def delete_person(session_id: str, person_id: str):
    session = rs.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    if session.get("status") != "assigning":
        raise HTTPException(400, "People can only be removed before payment collection starts")

    people = session.get("people", [])
    idx = next((i for i, person in enumerate(people) if person.get("id") == person_id), None)
    if idx is None:
        raise HTTPException(404, "Person not found")

    removed = people.pop(idx)
    rs._save(session)
    return {
        "ok": True,
        "removed": removed,
        "session": session,
        "assignment": _assignment_summary(session),
    }


@app.delete("/session/{session_id}")
async def delete_session(session_id: str):
    """Delete a session (for demo resets)."""
    path = Path(__file__).parent / "sessions" / f"{session_id}.json"
    if path.exists():
        path.unlink()
    if session_id in rs._cache:
        del rs._cache[session_id]
    return {"ok": True}


@app.get("/sessions")
async def list_sessions():
    return {"ok": True, "sessions": rs.list_sessions()}


@app.get("/pay/{session_id}/{person_id}", response_class=HTMLResponse)
async def payment_portal(session_id: str, person_id: str):
    session = rs.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    person = next((p for p in session["people"] if p["id"] == person_id), None)
    if not person:
        raise HTTPException(404, "Person not found")

    amount     = float(person.get("amount") or 0)
    name       = person.get("name", "Guest")
    restaurant = session.get("restaurant_name", "Restaurant")
    paid       = bool(person.get("paid"))
    real_url   = person.get("bunqme_url") or ""
    is_real    = bool(real_url and not person.get("request_simulated") and "demo_" not in real_url)

    simulate_url = f"/pay/{session_id}/{person_id}/simulate"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>Round — Pay your share</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></script>
<style>
  @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600;700&display=swap');
  :root {{
    --bg:      #0a0a0a;
    --card:    #141414;
    --border:  rgba(255,255,255,0.10);
    --text:    #f5f5f5;
    --muted:   #888;
    --orange:  #ff5c00;
    --green:   #00c781;
    --blue:    #1a8cff;
    --pink:    #e040fb;
    --radius:  18px;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: 'DM Sans', sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: flex-start;
    padding: 0 0 40px;
  }}

  /* ── TOP BAR ── */
  .topbar {{
    width: 100%;
    padding: 18px 24px;
    display: flex;
    align-items: center;
    justify-content: center;
    border-bottom: 1px solid var(--border);
  }}
  .bunq-logo {{
    font-size: 1.5rem;
    font-weight: 700;
    color: var(--text);
    letter-spacing: -0.04em;
  }}

  /* ── CARD ── */
  .card {{
    width: min(420px, 100%);
    margin: 32px auto 0;
    padding: 28px 24px;
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: var(--radius);
  }}

  /* ── AVATAR + REQUESTER ── */
  .requester {{
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 10px;
    margin-bottom: 24px;
  }}
  .avatar {{
    width: 64px;
    height: 64px;
    border-radius: 50%;
    background: conic-gradient(#ff5c00 0%, #e040fb 33%, #1a8cff 66%, #00c781 100%);
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 1.6rem;
  }}
  .requester-name {{ font-size: 1.1rem; font-weight: 600; }}
  .requester-iban {{ font-size: 0.72rem; color: var(--muted); font-family: monospace; }}
  .request-desc {{
    font-size: 0.88rem;
    color: var(--muted);
    text-align: center;
    line-height: 1.5;
    margin-bottom: 6px;
  }}

  /* ── AMOUNT ── */
  .amount-display {{
    text-align: center;
    margin-bottom: 28px;
  }}
  .amount-val {{
    font-size: 3.2rem;
    font-weight: 700;
    color: var(--orange);
    letter-spacing: -0.05em;
    line-height: 1;
  }}
  .amount-cents {{ font-size: 1.8rem; vertical-align: super; }}

  /* ── PAY WITH ── */
  .pay-with-label {{
    font-size: 0.75rem;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-bottom: 10px;
  }}

  .method-row {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 16px;
    border-radius: 12px;
    border: 1px solid var(--border);
    margin-bottom: 8px;
    cursor: pointer;
    transition: border-color 0.15s, background 0.15s;
    text-decoration: none;
    color: var(--text);
  }}
  .method-row:hover {{ border-color: rgba(255,255,255,0.25); background: rgba(255,255,255,0.04); }}
  .method-row.disabled {{ opacity: 0.35; cursor: not-allowed; pointer-events: none; }}

  .method-left {{ display: flex; align-items: center; gap: 12px; }}
  .method-icon {{
    width: 38px;
    height: 38px;
    border-radius: 10px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 1.2rem;
    flex-shrink: 0;
  }}
  .icon-card    {{ background: #2a1f3d; }}
  .icon-ideal   {{ background: #0c1e3c; }}
  .icon-bancontact {{ background: #0e2040; }}
  .icon-bunq    {{ background: #1a0a00; }}

  .method-name {{ font-size: 0.95rem; font-weight: 500; }}
  .method-sub  {{ font-size: 0.72rem; color: var(--muted); margin-top: 1px; }}
  .method-arrow {{ color: var(--muted); font-size: 1.1rem; }}

  /* ── SIMULATE BUTTON ── */
  .simulate-btn {{
    width: 100%;
    padding: 16px;
    border-radius: 12px;
    border: 1.5px solid var(--orange);
    background: rgba(255,92,0,0.08);
    color: var(--orange);
    font-family: 'DM Sans', sans-serif;
    font-size: 0.95rem;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.15s;
    margin-bottom: 8px;
  }}
  .simulate-btn:hover {{ background: rgba(255,92,0,0.15); }}
  .simulate-btn:disabled {{ opacity: 0.4; cursor: not-allowed; }}

  /* ── PAID STATE ── */
  .paid-banner {{
    background: rgba(0,199,129,0.1);
    border: 1px solid rgba(0,199,129,0.3);
    border-radius: 12px;
    padding: 18px;
    text-align: center;
    color: var(--green);
    font-size: 1rem;
    font-weight: 600;
    margin-bottom: 16px;
  }}

  /* ── FOOTER ── */
  .footer-links {{
    display: flex;
    justify-content: center;
    gap: 20px;
    margin-top: 24px;
    font-size: 0.75rem;
    color: var(--muted);
  }}
  .footer-links a {{ color: var(--muted); text-decoration: underline; }}
  .footer-note {{
    text-align: center;
    font-size: 0.72rem;
    color: var(--muted);
    margin-top: 16px;
    line-height: 1.5;
  }}

  /* ── QR SECTION ── */
  .qr-section {{
    display: flex;
    flex-direction: column;
    align-items: center;
    margin: 16px 0;
    gap: 10px;
  }}
  #qr-canvas {{
    background: white;
    padding: 10px;
    border-radius: 12px;
  }}
  .qr-hint {{ font-size: 0.75rem; color: var(--muted); }}

  /* ── SPINNER ── */
  @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
  .spinner {{
    display: inline-block;
    width: 16px; height: 16px;
    border: 2px solid rgba(255,255,255,0.2);
    border-top-color: var(--orange);
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
    vertical-align: middle;
    margin-right: 6px;
  }}

  /* ── SUCCESS OVERLAY ── */
  #success-overlay {{
    display: none;
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.85);
    z-index: 100;
    align-items: center;
    justify-content: center;
    flex-direction: column;
    gap: 16px;
    text-align: center;
    padding: 24px;
  }}
  #success-overlay.show {{ display: flex; }}
  .success-check {{ font-size: 5rem; }}
  .success-title {{ font-size: 1.8rem; font-weight: 700; color: var(--green); }}
  .success-sub {{ font-size: 1rem; color: var(--muted); max-width: 28ch; }}
  .success-close {{
    margin-top: 8px;
    padding: 12px 28px;
    border-radius: 12px;
    background: var(--green);
    color: #000;
    font-family: 'DM Sans', sans-serif;
    font-size: 0.9rem;
    font-weight: 700;
    border: none;
    cursor: pointer;
  }}
</style>
</head>
<body>

<div class="topbar">
  <span class="bunq-logo">bunq</span>
</div>

<div class="card">
  {"<div class='paid-banner'>✅ Payment already completed</div>" if paid else ""}

  <div class="requester">
    <div class="avatar">🍽️</div>
    <div class="requester-name">Round — {restaurant}</div>
    <div class="requester-iban">NL38 BUNQ 2106 2207 15</div>
    <p class="request-desc">
      sent you a payment request for<br>
      <strong>"{name}'s share at {restaurant}"</strong>
    </p>
  </div>

  <div class="amount-display">
    <div class="amount-val">
      &euro;{int(amount)}<span class="amount-cents">.{int(round((amount % 1) * 100)):02d}</span>
    </div>
  </div>

  {"" if paid else f'''
  <div class="pay-with-label">Pay with:</div>

  {"<a class='method-row' href='" + real_url + "' target='_blank'>" if is_real else "<div class='method-row disabled'>"}
    <div class="method-left">
      <div class="method-icon icon-card">💳</div>
      <div>
        <div class="method-name">Credit or Debit Card</div>
        <div class="method-sub">Arrives immediately</div>
      </div>
    </div>
    <span class="method-arrow">›</span>
  {"</a>" if is_real else "</div>"}

  {"<a class='method-row' href='" + real_url + "' target='_blank'>" if is_real else "<div class='method-row disabled'>"}
    <div class="method-left">
      <div class="method-icon icon-ideal">🏦</div>
      <div>
        <div class="method-name">iDEAL</div>
        <div class="method-sub">Arrives immediately</div>
      </div>
    </div>
    <span class="method-arrow">›</span>
  {"</a>" if is_real else "</div>"}

  {"<a class='method-row' href='" + real_url + "' target='_blank'>" if is_real else "<div class='method-row disabled'>"}
    <div class="method-left">
      <div class="method-icon icon-bancontact">🔵</div>
      <div>
        <div class="method-name">Bancontact</div>
        <div class="method-sub">Arrives immediately</div>
      </div>
    </div>
    <span class="method-arrow">›</span>
  {"</a>" if is_real else "</div>"}

  <div style="height:12px"></div>

  <button class="simulate-btn" id="sim-btn" onclick="simulatePay()" {"disabled" if paid else ""}>
    {'✅ Already Paid' if paid else '🧪 Pay with bunq (sandbox demo)'}
  </button>

  {"<div class='qr-section'><div id='qr-canvas'></div><div class='qr-hint'>Or scan to open payment link</div></div>" if is_real else ""}
  '''}
</div>

<div class="footer-links">
  <a href="#">See how it works</a>
  <a href="#">Report misuse</a>
</div>
<p class="footer-note">
  This site is protected by bunq.<br>
  <a href="#" style="color:var(--muted)">Privacy Policy</a> · <a href="#" style="color:var(--muted)">Terms of Service</a>
</p>

<!-- Success overlay -->
<div id="success-overlay">
  <div class="success-check">✅</div>
  <div class="success-title">Payment sent!</div>
  <div class="success-sub" id="success-msg">€{amount:.2f} has been sent. You're all square!</div>
  <button class="success-close" onclick="document.getElementById('success-overlay').classList.remove('show')">
    Close
  </button>
</div>

<script>
{"new QRCode(document.getElementById('qr-canvas'), {text: " + repr(real_url) + ", width:160,height:160,colorDark:'#000',colorLight:'#fff',correctLevel:QRCode.CorrectLevel.H});" if is_real else ""}

async function simulatePay() {{
  const btn = document.getElementById('sim-btn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Sending to bunq sandbox...';

  try {{
    const r = await fetch('{simulate_url}', {{ method: 'POST' }});
    const d = await r.json();
    if (!d.ok) throw new Error(d.detail || 'Payment failed');

    document.getElementById('success-msg').textContent =
      '€{amount:.2f} sent via bunq sandbox. {name} is all square!';
    document.getElementById('success-overlay').classList.add('show');

    btn.innerHTML = '✅ Paid';
    btn.style.borderColor = 'var(--green)';
    btn.style.color = 'var(--green)';
    btn.style.background = 'rgba(0,199,129,0.08)';

  }} catch(e) {{
    btn.disabled = false;
    btn.innerHTML = '🧪 Pay with bunq (sandbox demo)';
    alert('Error: ' + e.message);
  }}
}}
</script>
</body>
</html>"""
    return HTMLResponse(html)


@app.post("/pay/{session_id}/{person_id}/simulate")
async def simulate_payment(session_id: str, person_id: str):
    """
    Simulate a bunq payment for demo purposes.
    1. Sends real payment to sugardaddy (bunq sandbox)
    2. Marks person as paid in session
    3. Checks if all paid → triggers restaurant release
    """
    session = rs.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    person = next((p for p in session["people"] if p["id"] == person_id), None)
    if not person:
        raise HTTPException(404, "Person not found")

    if person.get("paid"):
        return {"ok": True, "message": "Already paid", "all_paid": rs.all_paid(session)}

    amount = float(person.get("amount") or 0)

    # Make real bunq payment to sugardaddy
    try:
        rb._ensure_ready()
        import bunq_client as bc
        bc.make_payment(
            state=rb._state,
            private_pem=rb._private_pem,
            account_id=rb._account_id,
            amount=f"{amount:.2f}",
            description=f"Round: {person['name']} pays share at {session['restaurant_name']}",
            recipient_email="sugardaddy@bunq.com",
            recipient_name="Sugar Daddy",
        )
        print(f"[SIMULATE] ✅ Real bunq payment: €{amount:.2f} for {person['name']}")
    except Exception as e:
        print(f"[SIMULATE] bunq payment failed ({e}), marking paid anyway for demo")

    # Mark as paid
    rs.mark_paid(session, person_id)

    all_done = rs.all_paid(session)

    # Auto-release if everyone paid
    if all_done and not session.get("released"):
        try:
            rb.pay_restaurant(
                amount=session["total"],
                restaurant_email=session.get("restaurant_email", "sugardaddy@bunq.com"),
                restaurant_name=session.get("restaurant_name", "Restaurant"),
                description=f"Round — {session['restaurant_name']} — full table payment",
            )
            session["released"] = True
            session["status"] = "complete"
            rs._save(session)
            print(f"[SIMULATE] 🎉 All paid — restaurant payment released: €{session['total']:.2f}")
        except Exception as e:
            print(f"[SIMULATE] Restaurant release failed: {e}")

    return {
        "ok":       True,
        "person":   person["name"],
        "amount":   amount,
        "all_paid": all_done,
        "released": session.get("released", False),
        "message":  f"€{amount:.2f} sent for {person['name']}. {'All settled! Restaurant paid.' if all_done else 'Waiting for others.'}",
    }


@app.get("/demo-portal", response_class=HTMLResponse)
async def demo_portal():
    """
    Preview the payment portal without going through the full flow.
    Hit http://localhost:8000/demo-portal to see how it looks.
    """
    # Inject a fake person into a fake session for preview
    fake_session_id = "preview0"
    fake_person_id  = "prev01"

    existing = rs.get_session(fake_session_id)
    if not existing:
        s = rs.new_session("La Bella Italia", [{"name": "Pasta Carbonara", "price": 16.0}], 22.50)
        s["id"] = fake_session_id
        rs.add_person(s, "Nithin", [{"name": "Pasta Carbonara", "price": 16.0}], 22.50, "nithin@example.com")
        s["people"][0]["id"] = fake_person_id
        rs._save(s)

    from fastapi.responses import RedirectResponse
    return RedirectResponse(f"/pay/{fake_session_id}/{fake_person_id}")


# ---------------------------------------------------------------------------
# Background polling loop
# ---------------------------------------------------------------------------

_polling: set[str] = set()


def _poll_loop(session_id: str):
    """Poll bunq every 8 seconds until all paid or 10 minutes elapsed."""
    if session_id in _polling:
        return
    _polling.add(session_id)

    deadline = time.time() + 600  # 10 minutes
    while time.time() < deadline:
        time.sleep(8)
        session = rs.get_session(session_id)
        if not session or session.get("status") == "complete":
            break
        try:
            newly_paid = rb.poll_session_payments(session)
            for pid in newly_paid:
                rs.mark_paid(session, pid)
                print(f"[POLL] ✅ {pid} paid")
            rs._save(session)
            if rs.all_paid(session):
                print(f"[POLL] 🎉 All paid for session {session_id}")
                break
        except Exception as e:
            print(f"[POLL] Error: {e}")

    _polling.discard(session_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _unassigned_total(session: dict) -> float:
    assigned = sum(p["amount"] for p in session["people"])
    return round(session["total"] - assigned, 2)


def _payment_portal_url(session_id: str, person_id: str) -> str:
    return f"/pay/{session_id}/{person_id}"


def _normalize_item_name(name: str) -> str:
    text = str(name or "").strip().lower()
    text = re.sub(r"^\d+\s*[xX]\s+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def _expand_receipt_items(receipt_items: list[dict]) -> list[dict]:
    expanded = []
    for receipt_idx, item in enumerate(receipt_items or []):
        raw_name = str(item.get("name", "")).strip()
        price = round(float(item.get("price", 0) or 0), 2)
        match = re.match(r"^(\d+)[xX]\s+(.+)$", raw_name)
        if match:
            qty = max(1, int(match.group(1)))
            base_name = match.group(2).strip()
            unit_price = round(price / qty, 2)
            for unit_idx in range(qty):
                expanded.append({
                    "claim_id": f"{receipt_idx}:{unit_idx}",
                    "name": base_name,
                    "price": unit_price,
                    "receipt_idx": receipt_idx,
                    "unit_idx": unit_idx,
                    "original_name": raw_name,
                })
        else:
            expanded.append({
                "claim_id": f"{receipt_idx}:0",
                "name": raw_name,
                "price": price,
                "receipt_idx": receipt_idx,
                "unit_idx": 0,
                "original_name": raw_name,
            })
    return expanded


def _claimed_item_ids(session: dict) -> set[str]:
    claimed = set()
    for person in session.get("people", []):
        for item in person.get("items", []):
            claim_id = item.get("claim_id")
            if claim_id:
                claimed.add(claim_id)
    return claimed


def _allocate_matched_items(session: dict, matched_items: list[dict]) -> list[dict]:
    if not matched_items:
        return []

    claimed_ids = _claimed_item_ids(session)
    available_by_key: dict[tuple[str, float], list[dict]] = {}
    totals_by_key: dict[tuple[str, float], int] = {}

    for item in _expand_receipt_items(session.get("items") or []):
        key = (_normalize_item_name(item["name"]), round(float(item["price"]), 2))
        totals_by_key[key] = totals_by_key.get(key, 0) + 1
        if item["claim_id"] in claimed_ids:
            continue
        available_by_key.setdefault(key, []).append(item)

    shortages: list[str] = []
    allocated: list[dict] = []
    for matched in matched_items:
        name = str(matched.get("name", "")).strip()
        price = round(float(matched.get("price", 0) or 0), 2)
        key = (_normalize_item_name(name), price)
        slot = (available_by_key.get(key) or [])
        if not slot:
            total_count = totals_by_key.get(key, 0)
            if total_count > 0:
                shortages.append(f"{name} is already fully assigned")
            else:
                shortages.append(f"{name} is not available on this bill")
            continue

        picked = slot.pop(0)
        allocated.append({
            "claim_id": picked["claim_id"],
            "name": picked["name"],
            "price": picked["price"],
            "receipt_idx": picked["receipt_idx"],
            "unit_idx": picked["unit_idx"],
            "original_name": picked["original_name"],
        })

    if shortages:
        raise HTTPException(409, ". ".join(dict.fromkeys(shortages)))

    return allocated


def _assignment_summary(session: dict) -> dict:
    expanded_items = _expand_receipt_items(session.get("items") or [])
    claimed_ids = _claimed_item_ids(session)
    assigned_total = round(sum(float(person.get("amount", 0) or 0) for person in session.get("people", [])), 2)
    remaining_total = round(max(0.0, float(session.get("total", 0) or 0) - assigned_total), 2)

    return {
        "total": round(float(session.get("total", 0) or 0), 2),
        "assigned": assigned_total,
        "remaining": remaining_total,
        "people_count": len(session.get("people", [])),
        "items_total": len(expanded_items),
        "items_assigned": sum(1 for item in expanded_items if item["claim_id"] in claimed_ids),
        "items_remaining": sum(1 for item in expanded_items if item["claim_id"] not in claimed_ids),
        "fully_assigned": remaining_total <= 0.01,
    }
