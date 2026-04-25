# Round 🍽️ — Split the bill in one round

Receipt scan → voice assignment → bunq payment requests → auto-release to restaurant.

## Setup (one time)

```bash
# 1. Install deps
# Use Python 3.10 and a fresh venv. PaddleOCR 2.x in this repo is pinned
# because PaddleOCR 3.x is not compatible with the current OCR code.
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt -c constraints.txt

# 2. Make sure bunq is already set up (from receipt_module)
cd ../receipt_module
python bunq_client.py setup     # if not done already
python bunq_client.py topup     # get sandbox money
cd ../round

# 3. Make sure Ollama is running (separate terminal)
ollama serve

# 4. Start the Round API
uvicorn round_api:app --reload --port 8000

# 5. Open the UI
# → http://localhost:8000
```

## How to demo

1. Open http://localhost:8000
2. Upload a restaurant receipt photo
3. For each person:
   - Enter their name + bunq email
   - Click 🎤 and say "I had the burger and a beer"
   - OR switch to text mode and type it
4. Click "Send payment requests"
5. Each person gets a bunq.me link — click "Show QR" to display
6. As people pay, the pot fills up
7. When all paid → restaurant is paid automatically

## Manual demo (simulate payments)

If you want to demo without real payments, use the API directly:

```bash
# Mark someone as paid manually
curl -X POST http://localhost:8000/session/{session_id}/mark-paid/{person_id}

# Check session status
curl http://localhost:8000/session/{session_id}

# Trigger release manually
curl -X POST http://localhost:8000/release/{session_id} \
  -H "Content-Type: application/json" \
  -d '{"session_id": "{session_id}", "restaurant_email": "sugardaddy@bunq.com", "restaurant_name": "The Restaurant"}'
```

## File structure

```
round/
  round_api.py       ← FastAPI backend (run this)
  round_session.py   ← Session state management
  round_bunq.py      ← bunq API: payment requests + polling
  round_voice.py     ← Whisper transcription + LLM item matching
  round_ocr.py       ← Receipt scanning (uses existing OCR pipeline)
  requirements.txt
  sessions/          ← Auto-created, stores session JSON files
  ui/
    index.html       ← Entire frontend (one file)
```

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | /scan | Upload receipt image |
| POST | /assign/voice | Add person via voice |
| POST | /assign/text | Add person via text |
| POST | /collect | Send bunq payment requests |
| GET | /session/{id} | Get session status |
| POST | /poll/{id} | Check for new payments |
| POST | /release/{id} | Pay restaurant |
| POST | /session/{id}/mark-paid/{person_id} | Demo: manual pay |
| GET | /sessions | List all sessions |

## Troubleshooting

**"OCR failed"** → Recreate the venv and reinstall pinned deps:
`python -m venv venv`
`venv\Scripts\activate`
`pip install -r requirements.txt -c constraints.txt`

If `venv\Scripts\python.exe` says it cannot find a base Python, the venv was created from a Python install that no longer exists and must be recreated.

**"Transcription failed"** → Check Whisper: `pip install openai-whisper`
Also needs ffmpeg: `sudo apt install ffmpeg` / `brew install ffmpeg`

On Windows, if you see an error mentioning `torch\\lib\\shm.dll` or `WinError 127`,
recreate the venv and reinstall the pinned voice stack:
`Remove-Item -Recurse -Force .\venv`
`py -3.10 -m venv venv`
`venv\Scripts\activate`
`pip install -r requirements.txt -c constraints.txt`

**"No bunq accounts"** → Run `python ../receipt_module/bunq_client.py topup`

**"Session expired"** → Run `python ../receipt_module/bunq_client.py session`

**Ollama errors** → Make sure `ollama serve` is running in another terminal
