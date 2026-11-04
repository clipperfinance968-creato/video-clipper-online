import os
import uuid
import shutil
import asyncio
import subprocess
from pathlib import Path
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
DOWNLOAD_DIR = Path(os.getenv("TEMP_STORAGE_PATH", "/app/downloads"))
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

COOKIES_PATH = os.getenv("YOUTUBE_COOKIES_PATH", "")
MAX_DURATION = int(os.getenv("MAX_VIDEO_DURATION", "3600"))
RETENTION = int(os.getenv("FILE_RETENTION_MINUTES", "30"))

app = FastAPI(title="Video Clipper Online")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
templates = Jinja2Templates(directory=ROOT / "templates")

jobs = {}

class ClipRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    start: str
    end: str
    quality: str = "720"

def timestamp(value):
    parts = value.strip().split(":")
    if not 1 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        raise ValueError("Format timestamp tidak valid.")
    nums = list(map(int, parts))
    if len(nums) == 1:
        return nums[0]
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    return nums[0] * 3600 + nums[1] * 60 + nums[2]

def validate_url(url):
    host = (urlparse(url).hostname or "").lower()
    allowed = {
        "youtube.com", "www.youtube.com",
        "m.youtube.com", "youtu.be"
    }
    if urlparse(url).scheme not in ("http", "https") or host not in allowed:
        raise ValueError("URL YouTube tidak valid.")

def run_command(command):
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=7200
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout)[-2000:])

def cleanup():
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=RETENTION)
    for item in DOWNLOAD_DIR.iterdir():
        try:
            modified = datetime.fromtimestamp(
                item.stat().st_mtime, timezone.utc
            )
            if modified < cutoff:
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink(missing_ok=True)
        except OSError:
            pass

async def process_clip(job_id, data):
    folder = DOWNLOAD_DIR / job_id
    folder.mkdir(parents=True, exist_ok=True)

    jobs[job_id]["status"] = "processing"

    try:
        start = timestamp(data.start)
        end = timestamp(data.end)

        if end <= start:
            raise ValueError("Timestamp akhir harus lebih besar.")
        if end - start > MAX_DURATION:
            raise ValueError("Durasi potongan melebihi batas.")

        source_template = str(folder / "source.%(ext)s")

        height = data.quality
        command = [
            "yt-dlp",
            "--no-playlist",
            "-f",
            f"bv*[height<={height}]+ba/b[height<={height}]/b",
            "--merge-output-format", "mp4",
            "-o", source_template
        ]

        if COOKIES_PATH and Path(COOKIES_PATH).is_file():
            command += ["--cookies", COOKIES_PATH]

        command.append(data.url)

        await asyncio.to_thread(run_command, command)

        sources = list(folder.glob("source.*"))
        if not sources:
            raise RuntimeError("Video sumber tidak ditemukan.")

        output = folder / "clip.mp4"

        ffmpeg = [
            "ffmpeg", "-y",
            "-ss", str(start),
            "-i", str(sources[0]),
            "-t", str(end - start),
            "-map", "0:v:0?",
            "-map", "0:a:0?",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "23",
            "-c:a", "aac",
            "-movflags", "+faststart",
            str(output)
        ]

        await asyncio.to_thread(run_command, ffmpeg)

        shutil.rmtree(folder / "temporary", ignore_errors=True)

        for source in sources:
            source.unlink(missing_ok=True)

        jobs[job_id]["status"] = "completed"

    except Exception as error:
        jobs[job_id].update(
            status="failed",
            error=str(error)[-1800:]
        )
        shutil.rmtree(folder, ignore_errors=True)

@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {"request": request}
    )

@app.get("/api/health")
async def health():
    return {"status": "ok"}

@app.post("/api/jobs")
async def create_job(data: ClipRequest, background_tasks: BackgroundTasks):
    try:
        validate_url(data.url)
        start = timestamp(data.start)
        end = timestamp(data.end)

        if end <= start:
            raise ValueError("Timestamp akhir harus lebih besar.")
        if end - start > MAX_DURATION:
            raise ValueError("Durasi potongan terlalu panjang.")

    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))

    cleanup()

    job_id = uuid.uuid4().hex

    jobs[job_id] = {
        "status": "queued",
        "error": None
    }

    background_tasks.add_task(process_clip, job_id, data)

    return {
        "job_id": job_id,
        "status": "queued"
    }

@app.get("/api/jobs/{job_id}")
async def job_status(job_id: str):
    job = jobs.get(job_id)

    if not job:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan.")

    result = {
        "job_id": job_id,
        "status": job["status"]
    }

    if job.get("error"):
        result["error"] = job["error"]

    if job["status"] == "completed":
        result["download_url"] = f"/api/jobs/{job_id}/download"

    return result

@app.get("/api/jobs/{job_id}/download")
async def download(job_id: str):
    file = DOWNLOAD_DIR / job_id / "clip.mp4"

    if not file.is_file():
        raise HTTPException(status_code=404, detail="File tidak tersedia.")

    return FileResponse(
        file,
        media_type="video/mp4",
        filename="video-clip.mp4"
    )
