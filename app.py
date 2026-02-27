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
app.config['MAX_CONTENT_LENGTH'] = 4 * 1024 * 1024 * 1024  # 2GB max upload

CLIPS_DIR = Path("/tmp/clips")
CLIPS_DIR.mkdir(exist_ok=True)
UPLOADS_DIR = Path("/tmp/uploads")
UPLOADS_DIR.mkdir(exist_ok=True)

jobs = {}
video_cache = {}   # YouTube URL cache
upload_cache = {}  # Uploaded file cache
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

def run_youtube_job(job_id, url, clips):
    try:
        clean_expired_cache()
        cached = get_cached_video(url, video_cache)

        if cached:
            jobs[job_id]["status"] = "cutting"
            jobs[job_id]["progress"] = "✓ Vídeo en caché — cortando directamente..."
            jobs[job_id]["percent"] = 100
            cut_clips(job_id, cached, clips)
            return

        jobs[job_id]["status"] = "downloading"
        jobs[job_id]["progress"] = "Conectando con YouTube..."
        jobs[job_id]["percent"] = 0
        video_path = CLIPS_DIR / f"video_{uuid.uuid4().hex[:8]}.mp4"

        cookies_path = Path("/app/cookies.txt")
        cmd = [
            "yt-dlp",
            "--no-check-certificates",
            "--extractor-args", "youtube:player_client=android",
            "--retries", "3",
            "-f", "best[ext=mp4]/best",
            "--newline",
            "-o", str(video_path),
        ]
        if cookies_path.exists():
            cmd += ["--cookies", str(cookies_path)]
        cmd.append(url)

        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        for line in process.stdout:
            line = line.strip()
            match = re.search(r'(\d+\.?\d*)%', line)
            if match:
                pct = float(match.group(1))
                jobs[job_id]["percent"] = round(pct)
                if pct < 50:
                    jobs[job_id]["progress"] = f"Descargando vídeo... {round(pct)}%"
                elif pct < 90:
                    jobs[job_id]["progress"] = f"Casi listo... {round(pct)}%"
                else:
                    jobs[job_id]["progress"] = f"Finalizando... {round(pct)}%"
            elif "Merging" in line:
                jobs[job_id]["progress"] = "Mezclando audio y vídeo..."
                jobs[job_id]["percent"] = 99

        dl_stderr = process.stderr.read()
        process.wait()

        if process.returncode != 0 or not video_path.exists():
            jobs[job_id]["status"] = "error"
            jobs[job_id]["error"] = dl_stderr[-600:] if dl_stderr else "Error desconocido al descargar"
            return

        video_cache[url] = {"path": str(video_path), "downloaded_at": time.time()}
        cut_clips(job_id, video_path, clips)

    except subprocess.TimeoutExpired:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = "Timeout: el vídeo tardó demasiado"
    except Exception as e:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = str(e)

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

@app.route("/api/upload", methods=["POST"])
def upload_video():
    if "video" not in request.files:
        return jsonify({"error": "No se recibió ningún archivo"}), 400
    file = request.files["video"]
    if file.filename == "":
        return jsonify({"error": "Nombre de archivo vacío"}), 400

    upload_id = uuid.uuid4().hex[:8]
    filename = f"{upload_id}_{secure_filename(file.filename)}"
    upload_path = UPLOADS_DIR / filename
    file.save(str(upload_path))

    # Save to upload cache (45 min)
    upload_cache[upload_id] = {
        "path": str(upload_path),
        "downloaded_at": time.time(),
        "name": file.filename
    }

    return jsonify({
        "upload_id": upload_id,
        "name": file.filename,
        "size": round(upload_path.stat().st_size / (1024*1024), 1)
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
    mode = data.get("mode", "upload")
    clips = data.get("clips", [])

    if not clips:
        return jsonify({"error": "Al menos un clip requerido"}), 400

    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {"status": "queued", "progress": "En cola...", "percent": 0, "files": [], "error": None}

    if mode == "youtube":
        url = data.get("url", "").strip()
        if not url:
            return jsonify({"error": "URL requerida"}), 400
        thread = threading.Thread(target=run_youtube_job, args=(job_id, url, clips), daemon=True)

    elif mode == "upload":
        upload_id = data.get("upload_id", "").strip()
        if not upload_id:
            return jsonify({"error": "Sube un vídeo primero"}), 400
        jobs[job_id]["status"] = "cutting"
        jobs[job_id]["progress"] = "Preparando clips..."
        thread = threading.Thread(target=run_upload_job, args=(job_id, upload_id, clips), daemon=True)

    else:
        return jsonify({"error": "Modo desconocido"}), 400

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
    from waitress import serve
    serve(app, host="0.0.0.0", port=port, connection_limit=100, cleanup_interval=30, channel_timeout=600)
