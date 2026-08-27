# Clip Generator (MVP)

Upload a long video (anime episode, podcast, stream VOD) → get back 1–3
auto-clipped, auto-captioned, watermarked 9:16 shorts, ready to post to
Reels/TikTok/YouTube Shorts.

## How it picks clips (v1 logic)
This MVP uses audio energy (RMS) over a sliding window to find the loudest /
most active moments in the source video, on the theory that dialogue peaks,
action, and music swells correlate with "interesting." It's a cheap, fast
proxy that needs zero ML and works offline. Swap in a real scene-detection
or LLM-based "find the funniest moment" model later — the `find_highlight_windows()`
function in `backend/processor.py` is the only place you'd need to change.

## Run it in GitHub Codespaces
1. Push this folder to a new GitHub repo.
2. On the repo page: **Code → Codespaces → Create codespace on main**.
3. The devcontainer auto-installs ffmpeg + Python deps (takes ~2 min the first time).
4. In the terminal:
   ```bash
   cd backend
   uvicorn main:app --reload --host 0.0.0.0 --port 8000
   ```
5. Codespaces will prompt to open port 8000 in the browser — that's your app.

## Run it locally instead
```bash
# needs ffmpeg installed (brew install ffmpeg / apt install ffmpeg)
cd backend
pip install -r requirements.txt
uvicorn main:app --reload
# open http://localhost:8000
```

## Project structure
```
.devcontainer/devcontainer.json   Codespaces config (ffmpeg + Python auto-setup)
backend/main.py                   FastAPI app: upload, process, download, free-tier limit
backend/processor.py              Core pipeline: highlight detection, crop, captions, watermark
frontend/index.html               Single-page upload UI (no build step needed)
```

## Monetization roadmap
The MVP ships with a simple free-tier cap (3 clips per browser, tracked via
cookie + `usage.json`) — see `FREE_CLIPS_PER_USER` in `backend/main.py`. That's
enough to validate demand. Once you see real usage:

1. **Add Stripe Checkout** for a paid tier (unlimited clips / month, or
   pay-per-clip credits). Stripe's Python SDK + a `/create-checkout-session`
   endpoint is ~30 lines — happy to build that next once you're ready.
2. **Move processing to a background job** (Celery, RQ, or just
   `BackgroundTasks` in FastAPI) instead of blocking the request — needed
   once videos get long or you have concurrent users.
3. **Swap the "tiny" Whisper model for "small"/"base"** once you're charging —
   better caption accuracy, still cheap on CPU.
4. **Add real scene detection** (e.g. TransNetV2 or an LLM pass over the
   transcript asking "which 30s span is most engaging") to improve clip
   quality — this is your actual product differentiation over time.
5. **Auto-post via platform APIs** (Instagram Graph API, TikTok Content
   Posting API) — biggest value-add, but requires app review from each
   platform, so treat as v2 once you have paying users, not before.

## Validate before building more
Use it yourself on Clipverse / animelines for 2–3 weeks. Track: minutes
saved per video, whether clip selection actually matches what you'd have
picked manually, and whether captions need much manual cleanup. Fix the
biggest gap before adding new features.
