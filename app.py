import os
import uuid
import threading
import subprocess
import time
import re
from flask import Flask, render_template, request, jsonify, send_file
from pathlib import Path

app = Flask(__name__)

CLIPS_DIR = Path("/tmp/clips")
CLIPS_DIR.mkdir(exist_ok=True)

jobs = {}
video_cache = {}
CACHE_MINUTES = 30

def time_to_seconds(minutes, seconds):
    return int(minutes) * 60 + int(seconds)

def get_cached_video(url):
    if url in video_cache:
        entry = video_cache[url]
        age_minutes = (time.time() - entry["downloaded_at"]) / 60
        if age_minutes < CACHE_MINUTES and Path(entry["path"]).exists():
            return Path(entry["path"])
        else:
            if Path(entry["path"]).exists():
                Path(entry["path"]).unlink()
            del video_cache[url]
    return None

def clean_expired_cache():
    expired = []
    for url, entry in video_cache.items():
        age_minutes = (time.time() - entry["downloaded_at"]) / 60
        if age_minutes >= CACHE_MINUTES:
            if Path(entry["path"]).exists():
                Path(entry["path"]).unlink()
            expired.append(url)
    for url in expired:
        del video_cache[url]

def run_clip_job(job_id, url, clips):
    job_dir = CLIPS_DIR / job_id
    job_dir.mkdir(exist_ok=True)
    output_files = []

    try:
        clean_expired_cache()
        cached = get_cached_video(url)

        if cached:
            jobs[job_id]["status"] = "cutting"
            jobs[job_id]["progress"] = "✓ Vídeo en caché — cortando directamente..."
            jobs[job_id]["percent"] = 100
            video_path = cached
        else:
            jobs[job_id]["status"] = "downloading"
            jobs[job_id]["progress"] = "Conectando con YouTube..."
            jobs[job_id]["percent"] = 0
            video_path = CLIPS_DIR / f"video_{uuid.uuid4().hex[:8]}.mp4"

            process = subprocess.Popen([
                "yt-dlp",
                "--no-check-certificates",
                "--extractor-retries", "3",
                "--retries", "3",
                "--cookies", "/app/cookies.txt",
                "-f", "best[ext=mp4]/best",
                "--newline",
                "-o", str(video_path),
                url
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

            dl_stderr = ""
            for line in process.stdout:
                line = line.strip()
                match = re.search(r'(\d+\.?\d*)%', line)
                if match:
                    pct = float(match.group(1))
                    jobs[job_id]["percent"] = round(pct)
                    if pct < 25:
                        jobs[job_id]["progress"] = f"Descargando vídeo... {round(pct)}%"
                    elif pct < 50:
                        jobs[job_id]["progress"] = f"Descargando vídeo... {round(pct)}%"
                    elif pct < 75:
                        jobs[job_id]["progress"] = f"Más de la mitad... {round(pct)}%"
                    elif pct < 95:
                        jobs[job_id]["progress"] = f"Casi listo... {round(pct)}%"
                    else:
                        jobs[job_id]["progress"] = f"Finalizando descarga... {round(pct)}%"
                elif "Merging" in line:
                    jobs[job_id]["progress"] = "Mezclando audio y vídeo..."
                    jobs[job_id]["percent"] = 99
                elif "Destination" in line:
                    jobs[job_id]["progress"] = "Preparando descarga..."

            dl_stderr = process.stderr.read()
            process.wait()

            if process.returncode != 0 or not video_path.exists():
                jobs[job_id]["status"] = "error"
                error_msg = dl_stderr[-600:] if dl_stderr else "Error desconocido al descargar"
                jobs[job_id]["error"] = error_msg
                return

            video_cache[url] = {
                "path": str(video_path),
                "downloaded_at": time.time()
            }

        # Cut clips
        jobs[job_id]["status"] = "cutting"
        total = len(clips)

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

    except subprocess.TimeoutExpired:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = "Timeout: el vídeo tardó demasiado"
    except Exception as e:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = str(e)


@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/start", methods=["POST"])
def start_job():
    data = request.json
    url = data.get("url", "").strip()
    clips = data.get("clips", [])
    if not url:
        return jsonify({"error": "URL requerida"}), 400
    if not clips:
        return jsonify({"error": "Al menos un clip requerido"}), 400
    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {"status": "queued", "progress": "En cola...", "percent": 0, "files": [], "error": None}
    thread = threading.Thread(target=run_clip_job, args=(job_id, url, clips), daemon=True)
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
