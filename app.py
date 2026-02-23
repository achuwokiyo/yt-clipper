import os
import uuid
import threading
import subprocess
from flask import Flask, render_template, request, jsonify, send_file
from pathlib import Path

app = Flask(__name__)

CLIPS_DIR = Path("/tmp/clips")
CLIPS_DIR.mkdir(exist_ok=True)

# Store job status in memory
jobs = {}

def time_to_seconds(minutes, seconds):
    return int(minutes) * 60 + int(seconds)

def run_clip_job(job_id, url, clips):
    job_dir = CLIPS_DIR / job_id
    job_dir.mkdir(exist_ok=True)
    output_files = []

    try:
        for i, clip in enumerate(clips):
            jobs[job_id]["status"] = "downloading"
            jobs[job_id]["progress"] = f"Descargando clip {i+1} de {len(clips)}..."

            start_sec = time_to_seconds(clip["minutes"], clip["seconds"])
            duration = int(clip["duration"])
            # Add 2s buffer so ffmpeg copy has keyframe margin
            end_sec = start_sec + duration + 2
            clip_name = f"clip_{i+1:02d}_{clip['minutes']}m{clip['seconds']}s.mp4"
            clip_path = job_dir / clip_name
            temp_path = job_dir / f"temp_{i}.mp4"

            # Download ONLY the needed section (much faster)
            dl_result = subprocess.run([
                "yt-dlp",
                "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                "--merge-output-format", "mp4",
                "--download-sections", f"*{start_sec}-{end_sec}",
                "-o", str(temp_path),
                url
            ], capture_output=True, text=True, timeout=120)

            if dl_result.returncode != 0 or not temp_path.exists():
                jobs[job_id]["status"] = "error"
                jobs[job_id]["error"] = f"Error en clip {i+1}: {dl_result.stderr[-300:]}"
                return

            jobs[job_id]["progress"] = f"Cortando clip {i+1} de {len(clips)}..."

            # Trim exactly to requested duration
            cut_result = subprocess.run([
                "ffmpeg", "-y",
                "-i", str(temp_path),
                "-t", str(duration),
                "-c", "copy",
                str(clip_path)
            ], capture_output=True, text=True, timeout=60)

            # Clean temp file
            if temp_path.exists():
                temp_path.unlink()

            if clip_path.exists():
                output_files.append({
                    "name": clip_name,
                    "path": str(clip_path),
                    "size": round(clip_path.stat().st_size / (1024*1024), 1)
                })

        jobs[job_id]["status"] = "done"
        jobs[job_id]["files"] = output_files
        jobs[job_id]["progress"] = f"✓ {len(output_files)} clips listos"

    except subprocess.TimeoutExpired:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = "Timeout: el clip tardó demasiado"
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
    jobs[job_id] = {
        "status": "queued",
        "progress": "En cola...",
        "files": [],
        "error": None
    }

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
