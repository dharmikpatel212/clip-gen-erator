import json
import os
import shutil
import uuid
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Cookie, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from processor import process_video

BASE_DIR = Path(__file__).resolve().parent.parent
UPLOADS_DIR = BASE_DIR / "uploads"
OUTPUTS_DIR = BASE_DIR / "outputs"
USAGE_FILE = BASE_DIR / "usage.json"
FREE_CLIPS_PER_USER = 3  # simple freemium cap - see README "Monetization" section

UPLOADS_DIR.mkdir(exist_ok=True)
OUTPUTS_DIR.mkdir(exist_ok=True)

app = FastAPI(title="Clip Generator API")


def _load_usage() -> dict:
    if USAGE_FILE.exists():
        return json.loads(USAGE_FILE.read_text())
    return {}


def _save_usage(usage: dict) -> None:
    USAGE_FILE.write_text(json.dumps(usage))


@app.post("/process")
async def process(
    response: Response,
    file: UploadFile = File(...),
    num_clips: int = Form(3),
    watermark_text: str = Form("@yourhandle"),
    user_id: str | None = Cookie(default=None),
):
    # --- lightweight freemium gate ---
    if user_id is None:
        user_id = uuid.uuid4().hex
        response.set_cookie("user_id", user_id, max_age=60 * 60 * 24 * 365)

    usage = _load_usage()
    used = usage.get(user_id, 0)
    if used + num_clips > FREE_CLIPS_PER_USER:
        raise HTTPException(
            status_code=402,
            detail=(
                f"Free tier limit reached ({FREE_CLIPS_PER_USER} clips). "
                "Upgrade to keep generating clips."
            ),
        )

    if not file.filename.lower().endswith((".mp4", ".mov", ".mkv", ".webm")):
        raise HTTPException(status_code=400, detail="Unsupported file type")

    job_id = uuid.uuid4().hex[:10]
    upload_path = UPLOADS_DIR / f"{job_id}_{file.filename}"
    with open(upload_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    job_out_dir = OUTPUTS_DIR / job_id
    try:
        clip_paths = process_video(
            str(upload_path), str(job_out_dir),
            num_clips=num_clips, watermark_text=watermark_text,
        )
    finally:
        upload_path.unlink(missing_ok=True)

    usage[user_id] = used + len(clip_paths)
    _save_usage(usage)

    return {
        "job_id": job_id,
        "clips": [
            {"filename": Path(p).name, "url": f"/download/{job_id}/{Path(p).name}"}
            for p in clip_paths
        ],
        "clips_used": usage[user_id],
        "clips_remaining": max(0, FREE_CLIPS_PER_USER - usage[user_id]),
    }


@app.get("/download/{job_id}/{filename}")
async def download(job_id: str, filename: str):
    path = OUTPUTS_DIR / job_id / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path, media_type="video/mp4", filename=filename)


# Serves the simple upload UI at http://localhost:8000/
app.mount("/", StaticFiles(directory=str(BASE_DIR / "frontend"), html=True), name="frontend")
