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

    audio_bytes = await audio.read()

    # Transcribe
    try:
        transcript = rv.transcribe_bytes(audio_bytes)
    except Exception as e:
        raise HTTPException(500, f"Transcription failed: {e}")

    # Match items
    matched = rv.match_items(transcript, session["items"])
    amount = matched["total"]

    # Add to session
    person = rs.add_person(session, name, matched["matched_items"], amount, contact)

    return {
        "ok":         True,
        "person_id":  person["id"],
        "name":       name,
        "transcript": transcript,
        "items":      matched["matched_items"],
        "amount":     amount,
        "confidence": matched.get("confidence", 0),
        "remaining":  _unassigned_total(session),
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

    session_items = session.get("items") or []
    print(f"[ASSIGN/TEXT] session_id={body.session_id} items_in_session={session_items}")
    print(f"[ASSIGN/TEXT] input text='{body.text}'")

    if not session_items:
        raise HTTPException(400, f"Session has no items — OCR may have failed. Session items: {session_items!r}")

    matched = rv.match_items(body.text, session_items)
    amount = matched["total"]

    print(f"[ASSIGN/TEXT] matched={matched}")

    person = rs.add_person(session, body.name, matched["matched_items"], amount, body.contact)

    return {
        "ok":        True,
        "person_id": person["id"],
        "name":      body.name,
        "items":     matched["matched_items"],
        "amount":    amount,
        "confidence": matched.get("confidence", 0),
        "remaining": _unassigned_total(session),
        "debug_session_items": session_items,
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
        return {"ok": True, "message": "Already released"}

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

    payment_url = person.get("bunqme_url")
    simulated = person.get("request_simulated", False)
    method = person.get("request_method") or "bunqme"
    amount = float(person.get("amount") or 0)
    restaurant = session.get("restaurant_name", "Restaurant")
    person_name = person.get("name", "Guest")
    paid = bool(person.get("paid"))

    qr_script = ""
    qr_markup = '<div class="note">QR unavailable for this request.</div>'
    cta_markup = '<button class="primary" disabled>Payment Link Unavailable</button>'
    helper = "This portal opens the real bunq payment flow."

    if paid:
        helper = "This share has already been paid."
        qr_markup = '<div class="paid-pill">Paid</div>'
        cta_markup = '<button class="primary" disabled>Already Paid</button>'
    elif payment_url and not simulated:
        qr_markup = '<div id="qr"></div>'
        cta_markup = (
            f'<a class="primary" href="{payment_url}" target="_blank" rel="noopener noreferrer">'
            'Open bunq payment'
            "</a>"
        )
        qr_script = f"""
<script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></script>
<script>
new QRCode(document.getElementById('qr'), {{
  text: {payment_url!r},
  width: 192,
  height: 192,
  colorDark: '#000000',
  colorLight: '#ffffff',
  correctLevel: QRCode.CorrectLevel.H
}});
</script>
"""
    elif simulated:
        helper = "This request is using a demo fallback link, so the portal cannot open a real bunq checkout."

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Round Payment Portal</title>
  <style>
    :root {{
      --bg: #f4efe6; --ink: #141414; --muted: #6d665d; --card: #fffdf8;
      --line: #ded4c5; --accent: #0a7a4b; --accent-dark: #075f39; --soft: #efe6d8;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0; font-family: Georgia, "Times New Roman", serif; color: var(--ink);
      min-height: 100vh; display: grid; place-items: center; padding: 24px;
      background: radial-gradient(circle at top left, #fff8eb 0%, transparent 28%), linear-gradient(180deg, #f7f1e8 0%, var(--bg) 100%);
    }}
    .portal {{
      width: min(960px, 100%); display: grid; grid-template-columns: 1.1fr 0.9fr;
      border: 1px solid var(--line); background: var(--card); border-radius: 28px;
      overflow: hidden; box-shadow: 0 24px 80px rgba(0,0,0,0.08);
    }}
    .hero {{
      padding: 40px; border-right: 1px solid var(--line);
      background: linear-gradient(150deg, rgba(10,122,75,0.08), rgba(10,122,75,0.0)), repeating-linear-gradient(-45deg, transparent, transparent 14px, rgba(20,20,20,0.025) 14px, rgba(20,20,20,0.025) 28px);
    }}
    .eyebrow {{ display: inline-block; font-size: 12px; letter-spacing: 0.16em; text-transform: uppercase; color: var(--muted); margin-bottom: 18px; }}
    h1 {{ margin: 0 0 14px; font-size: clamp(34px, 5vw, 60px); line-height: 0.96; letter-spacing: -0.04em; }}
    .lede {{ margin: 0; font-size: 18px; line-height: 1.55; color: #2d2a25; max-width: 28ch; }}
    .meta {{ margin-top: 28px; display: grid; gap: 12px; }}
    .meta-row {{ display: flex; justify-content: space-between; gap: 20px; padding-bottom: 10px; border-bottom: 1px solid var(--line); font-size: 15px; }}
    .meta-label {{ color: var(--muted); }}
    .panel {{ padding: 32px 28px; display: flex; flex-direction: column; justify-content: space-between; gap: 18px; background: linear-gradient(180deg, #fffdfa 0%, #f8f3ea 100%); }}
    .amount {{ font-size: 52px; line-height: 1; letter-spacing: -0.06em; font-weight: 700; margin: 0; }}
    .method {{ display: inline-flex; align-items: center; gap: 8px; width: fit-content; padding: 8px 12px; border-radius: 999px; background: var(--soft); border: 1px solid var(--line); font-size: 13px; text-transform: uppercase; letter-spacing: 0.08em; color: #3f3a34; }}
    #qr {{ width: 216px; min-height: 216px; margin: 0 auto; padding: 12px; border-radius: 22px; background: #ffffff; border: 1px solid var(--line); display: grid; place-items: center; }}
    .actions {{ display: grid; gap: 12px; }}
    .primary, .secondary {{ display: inline-flex; justify-content: center; align-items: center; min-height: 52px; padding: 14px 18px; border-radius: 14px; text-decoration: none; border: none; font: inherit; cursor: pointer; }}
    .primary {{ background: var(--accent); color: #ffffff; }}
    .primary:hover {{ background: var(--accent-dark); }}
    .primary:disabled {{ background: #b9c5bf; cursor: not-allowed; }}
    .secondary {{ background: transparent; color: var(--ink); border: 1px solid var(--line); }}
    .note {{ padding: 16px; border-radius: 16px; background: #fff8ee; border: 1px solid #ecd6b7; color: #765631; font-size: 14px; line-height: 1.5; }}
    .paid-pill {{ display: inline-flex; justify-content: center; align-items: center; width: 216px; min-height: 216px; margin: 0 auto; border-radius: 22px; background: #eff8f2; border: 1px solid #bfddc8; color: var(--accent); font-size: 30px; font-weight: 700; }}
    .helper {{ color: var(--muted); font-size: 14px; line-height: 1.5; text-align: center; margin: 0; }}
    .urlbox {{ padding: 12px 14px; background: #fff; border: 1px solid var(--line); border-radius: 14px; font-size: 13px; color: #4e473f; word-break: break-all; }}
    @media (max-width: 820px) {{ .portal {{ grid-template-columns: 1fr; }} .hero {{ border-right: 0; border-bottom: 1px solid var(--line); }} h1 {{ font-size: 40px; }} }}
  </style>
</head>
<body>
  <main class="portal">
    <section class="hero">
      <div class="eyebrow">Round Payment Portal</div>
      <h1>{person_name}</h1>
      <p class="lede">Pay your share for <strong>{restaurant}</strong> using the real bunq checkout flow. bunq.me links can support bunq and iDEAL, depending on the link bunq returns.</p>
      <div class="meta">
        <div class="meta-row"><span class="meta-label">Session</span><strong>{session_id}</strong></div>
        <div class="meta-row"><span class="meta-label">Share</span><strong>EUR {amount:.2f}</strong></div>
        <div class="meta-row"><span class="meta-label">Method</span><strong>{method}</strong></div>
      </div>
    </section>
    <section class="panel">
      <div>
        <div class="method">bunq.me portal</div>
        <p class="amount">EUR {amount:.2f}</p>
      </div>
      {qr_markup}
      <div class="actions">
        {cta_markup}
        <button class="secondary" onclick="navigator.clipboard.writeText({(payment_url or '')!r})" {"disabled" if not payment_url else ""}>Copy payment link</button>
      </div>
      <p class="helper">{helper}</p>
      <div class="urlbox">{payment_url or 'No real payment link is available for this request yet.'}</div>
    </section>
  </main>
  {qr_script}
</body>
</html>"""
    return HTMLResponse(html)


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