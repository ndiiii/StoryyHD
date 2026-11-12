from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from flask import Flask, abort, jsonify, render_template, request, send_file
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("STORYHD_DATA_DIR", "/tmp/storyhd-data"))
UPLOAD_DIR = DATA_DIR / "uploads"
OUTPUT_DIR = DATA_DIR / "outputs"
for directory in (UPLOAD_DIR, OUTPUT_DIR):
    directory.mkdir(parents=True, exist_ok=True)

MAX_FILE_BYTES = int(os.environ.get("MAX_FILE_BYTES", str(300 * 1024 * 1024)))
MAX_DURATION_SECONDS = int(os.environ.get("MAX_DURATION_SECONDS", "180"))
JOB_TTL_SECONDS = int(os.environ.get("JOB_TTL_SECONDS", "1800"))
ALLOWED_EXTENSIONS = {"mp4", "mov", "m4v"}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_BYTES
app.config["JSON_SORT_KEYS"] = False

# One Gunicorn worker is used in the included deployment configuration so the
# in-memory job registry is shared by upload, polling, and download requests.
executor = ThreadPoolExecutor(max_workers=int(os.environ.get("PROCESSING_WORKERS", "2")))
MAX_ACTIVE_JOBS = int(os.environ.get("MAX_ACTIVE_JOBS", "4"))
jobs: dict[str, dict[str, Any]] = {}
jobs_lock = threading.Lock()


def run_command(args: list[str], timeout: int = 240) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def probe_video(path: Path) -> dict[str, Any]:
    result = run_command([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration,size,format_name:stream=codec_type,codec_name,width,height,r_frame_rate,bit_rate,sample_rate,channels",
        "-of", "json", str(path)
    ], timeout=30)
    data = json.loads(result.stdout or "{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video:
        raise ValueError("File ini tidak memiliki track video yang valid.")
    duration = float((data.get("format") or {}).get("duration") or 0)
    if duration <= 0:
        raise ValueError("Durasi video tidak dapat dibaca.")
    if duration > MAX_DURATION_SECONDS:
        raise ValueError(f"Durasi maksimal yang diizinkan adalah {MAX_DURATION_SECONDS // 60} menit.")
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    if not width or not height or width > 7680 or height > 7680:
        raise ValueError("Resolusi video tidak didukung.")
    rate = video.get("r_frame_rate", "-")
    try:
        numerator, denominator = rate.split("/")
        fps = round(float(numerator) / float(denominator), 2) if float(denominator) else None
    except (ValueError, ZeroDivisionError):
        fps = None
    fmt = data.get("format") or {}
    return {
        "duration": round(duration, 2),
        "size": int(fmt.get("size") or path.stat().st_size),
        "width": width,
        "height": height,
        "fps": fps,
        "video_codec": video.get("codec_name", "unknown"),
        "audio": any(s.get("codec_type") == "audio" for s in streams),
    }


def optimize_video(source: Path, destination: Path) -> dict[str, Any]:
    before = probe_video(source)

    # Keep the original orientation and aspect ratio. Downscale oversized media
    # to a sensible WhatsApp-friendly canvas, but never upscale small sources.
    scale_filter = (
        "scale=w='min(iw,if(gte(iw,ih),1920,1080))':"
        "h='min(ih,if(gte(iw,ih),1080,1920))':"
        "force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1"
    )
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source),
        "-map", "0:v:0", "-map", "0:a?",
        "-vf", scale_filter,
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-profile:v", "high", "-pix_fmt", "yuv420p", "-fps_mode", "passthrough",
        "-c:a", "aac", "-b:a", "160k", "-ac", "2",
        "-movflags", "+faststart", "-max_muxing_queue_size", "1024",
        "-sn", "-dn", str(destination),
    ]
    run_command(command, timeout=max(240, int(before["duration"] * 8)))
    after = probe_video(destination)
    return {"before": before, "after": after}


def cleanup_expired() -> None:
    now = time.time()
    with jobs_lock:
        expired = [job_id for job_id, item in jobs.items() if now - item["created_at"] > JOB_TTL_SECONDS]
        for job_id in expired:
            item = jobs.pop(job_id, {})
            for key in ("input_path", "output_path"):
                raw_path = item.get(key)
                if raw_path:
                    Path(raw_path).unlink(missing_ok=True)


def cleanup_loop() -> None:
    while True:
        time.sleep(60)
        try:
            cleanup_expired()
        except Exception:
            app.logger.exception("Temporary-file cleanup failed")


cleanup_thread = threading.Thread(target=cleanup_loop, name="storyhd-cleanup", daemon=True)
cleanup_thread.start()


def process_job(job_id: str, input_path: Path, output_path: Path) -> None:
    try:
        with jobs_lock:
            jobs[job_id]["status"] = "processing"
            jobs[job_id]["message"] = "Video sedang dioptimalkan di server."
        details = optimize_video(input_path, output_path)
        with jobs_lock:
            jobs[job_id].update({
                "status": "done",
                "message": "Video selesai dioptimalkan.",
                "details": details,
                "output_path": str(output_path),
            })
    except subprocess.TimeoutExpired:
        output_path.unlink(missing_ok=True)
        with jobs_lock:
            jobs[job_id].update({"status": "error", "message": "Pemrosesan melewati batas waktu. Coba video yang lebih pendek."})
    except subprocess.CalledProcessError as exc:
        output_path.unlink(missing_ok=True)
        detail = (exc.stderr or "").strip()[-500:]
        with jobs_lock:
            jobs[job_id].update({"status": "error", "message": "FFmpeg gagal memproses video. Pastikan file tidak rusak dan coba lagi.", "detail": detail})
    except Exception as exc:  # Return a safe message; keep diagnostics in server logs.
        output_path.unlink(missing_ok=True)
        app.logger.exception("StoryHD job %s failed", job_id)
        with jobs_lock:
            jobs[job_id].update({"status": "error", "message": str(exc) or "Terjadi kesalahan saat memproses video."})
    finally:
        input_path.unlink(missing_ok=True)


@app.get("/")
def index():
    cleanup_expired()
    return render_template("index.html", max_mb=MAX_FILE_BYTES // (1024 * 1024), max_minutes=MAX_DURATION_SECONDS // 60)


@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "StoryHD", "ffmpeg": shutil.which("ffmpeg") is not None, "ffprobe": shutil.which("ffprobe") is not None})


@app.post("/api/optimize")
def create_job():
    cleanup_expired()
    uploaded = request.files.get("video")
    if not uploaded or not uploaded.filename:
        return jsonify({"error": "Pilih video terlebih dahulu."}), 400
    safe_name = secure_filename(uploaded.filename)
    extension = Path(safe_name).suffix.lower().lstrip(".")
    if extension not in ALLOWED_EXTENSIONS:
        return jsonify({"error": "Format yang didukung: MP4, MOV, dan M4V."}), 400

    with jobs_lock:
        active_jobs = sum(1 for item in jobs.values() if item.get("status") in {"queued", "processing"})
        if active_jobs >= MAX_ACTIVE_JOBS:
            return jsonify({"error": "Server sedang memproses banyak video. Coba lagi beberapa saat."}), 429

    job_id = uuid.uuid4().hex
    input_path = UPLOAD_DIR / f"{job_id}.{extension}"
    output_path = OUTPUT_DIR / f"storyhd-{job_id}.mp4"
    uploaded.save(input_path)
    if input_path.stat().st_size == 0:
        input_path.unlink(missing_ok=True)
        return jsonify({"error": "File video kosong."}), 400

    try:
        source_info = probe_video(input_path)
    except (ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError):
        input_path.unlink(missing_ok=True)
        return jsonify({"error": "File tidak terbaca sebagai video yang valid."}), 400

    with jobs_lock:
        jobs[job_id] = {
            "status": "queued",
            "message": "Video diterima dan masuk antrean.",
            "created_at": time.time(),
            "input_path": str(input_path),
            "output_path": None,
            "source": source_info,
        }
    executor.submit(process_job, job_id, input_path, output_path)
    return jsonify({"job_id": job_id, "status": "queued", "source": source_info}), 202


@app.get("/api/jobs/<job_id>")
def job_status(job_id: str):
    cleanup_expired()
    with jobs_lock:
        item = jobs.get(job_id)
        if not item:
            return jsonify({"error": "Sesi tidak ditemukan atau sudah kedaluwarsa."}), 404
        response = {k: v for k, v in item.items() if k not in {"created_at", "input_path", "output_path", "detail"}}
        if item.get("status") == "done":
            response["download_url"] = f"/download/{job_id}"
        return jsonify(response)


@app.get("/preview/<job_id>")
def preview(job_id: str):
    cleanup_expired()
    with jobs_lock:
        item = jobs.get(job_id)
        if not item or item.get("status") != "done" or not item.get("output_path"):
            abort(404)
        output_path = Path(item["output_path"])
    if not output_path.is_file():
        abort(404)
    return send_file(output_path, as_attachment=False, mimetype="video/mp4", conditional=True)


@app.get("/download/<job_id>")
def download(job_id: str):
    cleanup_expired()
    with jobs_lock:
        item = jobs.get(job_id)
        if not item or item.get("status") != "done" or not item.get("output_path"):
            abort(404)
        output_path = Path(item["output_path"])
    if not output_path.is_file():
        abort(404)
    return send_file(output_path, as_attachment=True, download_name="storyhd-whatsapp.mp4", mimetype="video/mp4", conditional=True)


@app.errorhandler(413)
def file_too_large(_error):
    return jsonify({"error": f"Ukuran file terlalu besar. Batas saat ini {MAX_FILE_BYTES // (1024 * 1024)} MB."}), 413


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)
