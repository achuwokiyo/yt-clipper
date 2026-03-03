import os
import uuid
import threading
import subprocess
import time
import re
from flask import Flask, render_template, request, jsonify, send_file
from pathlib import Path
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 4 * 1024 * 1024 * 1024  # 4GB max upload

CLIPS_DIR = Path("/tmp/clips")
CLIPS_DIR.mkdir(exist_ok=True)
UPLOADS_DIR = Path("/tmp/uploads")
UPLOADS_DIR.mkdir(exist_ok=True)
CHUNKS_DIR = Path("/tmp/chunks")
CHUNKS_DIR.mkdir(exist_ok=True)

jobs = {}
video_cache = {}
upload_cache = {}
CACHE_MINUTES = 45

def time_to_seconds(minutes, seconds):
    return int(minutes) * 60 + int(seconds)

def get_cached_video(key, cache):
    if key in cache:
        entry = cache[key]
        age_minutes = (time.time() - entry["downloaded_at"]) / 60
        if age_minutes < CACHE_MINUTES and Path(entry["path"]).exists():
            return Path(entry["path"])
        else:
            if Path(entry["path"]).exists():
                Path(entry["path"]).unlink()
            del cache[key]
    return None

def clean_expired_cache():
    for cache in [video_cache, upload_cache]:
        expired = []
        for key, entry in cache.items():
            age_minutes = (time.time() - entry["downloaded_at"]) / 60
            if age_minutes >= CACHE_MINUTES:
                if Path(entry["path"]).exists():
                    Path(entry["path"]).unlink()
                expired.append(key)
        for key in expired:
            del cache[key]

def cut_clips(job_id, video_path, clips):
    job_dir = CLIPS_DIR / job_id
    job_dir.mkdir(exist_ok=True)
    output_files = []
    total = len(clips)

    jobs[job_id]["status"] = "cutting"
    for i, clip in enumerate(clips):
        jobs[job_id]["progress"] = f"Cortando clip {i+1} de {total}..."
        jobs[job_id]["percent"] = round((i / total) * 100)

        start_sec = time_to_seconds(clip["minutes"], clip["seconds"])
        duration = int(clip["duration"])
        clip_name = f"clip_{i+1:02d}_{clip['minutes']}m{clip['seconds']}s.mp4"
        clip_path = job_dir / clip_name

        subprocess.run([
            "ffmpeg", "-y",
            "-ss", str(start_sec),
            "-i", str(video_path),
            "-t", str(duration),
            "-c", "copy",
            str(clip_path)
        ], capture_output=True, text=True, timeout=60)

        if clip_path.exists():
            output_files.append({
                "name": clip_name,
                "path": str(clip_path),
                "size": round(clip_path.stat().st_size / (1024*1024), 1)
            })
            jobs[job_id]["progress"] = f"✓ Clip {i+1} listo" + (f" — cortando {i+2} de {total}..." if i+1 < total else "")
            jobs[job_id]["percent"] = round(((i+1) / total) * 100)

    jobs[job_id]["status"] = "done"
    jobs[job_id]["percent"] = 100
    jobs[job_id]["files"] = output_files
    jobs[job_id]["progress"] = f"✓ {len(output_files)} clips listos para descargar"

def run_upload_job(job_id, upload_id, clips):
    try:
        clean_expired_cache()
        video_path = get_cached_video(upload_id, upload_cache)
        if not video_path:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["error"] = "El vídeo ha expirado (45 min), sube de nuevo"
            return
        cut_clips(job_id, video_path, clips)
    except Exception as e:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = str(e)


@app.route("/")
def index():
    return render_template("index.html")

# ── Chunked upload endpoints ──

@app.route("/api/upload/start", methods=["POST"])
def upload_start():
    data = request.json
    filename = secure_filename(data.get("filename", "video.mp4"))
    total_chunks = int(data.get("total_chunks", 1))
    upload_id = uuid.uuid4().hex[:8]
    chunk_dir = CHUNKS_DIR / upload_id
    chunk_dir.mkdir(exist_ok=True)
    # Store metadata
    (chunk_dir / "meta.txt").write_text(f"{filename}\n{total_chunks}")
    return jsonify({"upload_id": upload_id})

@app.route("/api/upload/chunk", methods=["POST"])
def upload_chunk():
    upload_id = request.form.get("upload_id")
    chunk_index = int(request.form.get("chunk_index", 0))
    chunk_file = request.files.get("chunk")

    if not upload_id or not chunk_file:
        return jsonify({"error": "Faltan datos"}), 400

    chunk_dir = CHUNKS_DIR / upload_id
    if not chunk_dir.exists():
        return jsonify({"error": "Upload ID no válido"}), 400

    chunk_path = chunk_dir / f"chunk_{chunk_index:05d}"
    chunk_file.save(str(chunk_path))
    return jsonify({"ok": True, "chunk": chunk_index})

@app.route("/api/upload/finish", methods=["POST"])
def upload_finish():
    data = request.json
    upload_id = data.get("upload_id")
    filename = data.get("filename", "video.mp4")

    chunk_dir = CHUNKS_DIR / upload_id
    if not chunk_dir.exists():
        return jsonify({"error": "Upload ID no válido"}), 400

    # Read metadata
    meta = (chunk_dir / "meta.txt").read_text().split("\n")
    total_chunks = int(meta[1]) if len(meta) > 1 else 1

    # Assemble chunks
    safe_name = secure_filename(filename)
    final_path = UPLOADS_DIR / f"{upload_id}_{safe_name}"

    with open(str(final_path), "wb") as outfile:
        for i in range(total_chunks):
            chunk_path = chunk_dir / f"chunk_{i:05d}"
            if not chunk_path.exists():
                return jsonify({"error": f"Falta el chunk {i}"}), 400
            outfile.write(chunk_path.read_bytes())
            chunk_path.unlink()  # Free space as we go

    # Clean chunk dir
    import shutil
    shutil.rmtree(str(chunk_dir), ignore_errors=True)

    size_mb = round(final_path.stat().st_size / (1024*1024), 1)

    upload_cache[upload_id] = {
        "path": str(final_path),
        "downloaded_at": time.time(),
        "name": filename
    }

    return jsonify({
        "upload_id": upload_id,
        "name": filename,
        "size": size_mb
    })

@app.route("/api/recent")
def recent_uploads():
    clean_expired_cache()
    result = []
    for uid, entry in upload_cache.items():
        age_minutes = (time.time() - entry["downloaded_at"]) / 60
        remaining = round(CACHE_MINUTES - age_minutes)
        result.append({
            "upload_id": uid,
            "name": entry.get("name", uid),
            "remaining_minutes": remaining
        })
    return jsonify(result)

@app.route("/api/start", methods=["POST"])
def start_job():
    data = request.json
    clips = data.get("clips", [])
    if not clips:
        return jsonify({"error": "Al menos un clip requerido"}), 400

    upload_id = data.get("upload_id", "").strip()
    if not upload_id:
        return jsonify({"error": "Sube un vídeo primero"}), 400

    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {"status": "cutting", "progress": "Preparando clips...", "percent": 0, "files": [], "error": None}
    thread = threading.Thread(target=run_upload_job, args=(job_id, upload_id, clips), daemon=True)
    thread.start()
    return jsonify({"job_id": job_id})

@app.route("/api/status/<job_id>")
def job_status(job_id):
    if job_id not in jobs:
        return jsonify({"error": "Job no encontrado"}), 404
    return jsonify(jobs[job_id])

@app.route("/api/download/<job_id>/<filename>")
def download_file(job_id, filename):
    file_path = CLIPS_DIR / job_id / filename
    if not file_path.exists():
        return "Archivo no encontrado", 404
    return send_file(str(file_path), as_attachment=True, download_name=filename)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
