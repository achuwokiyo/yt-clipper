FROM python:3.11-slim

# Install ffmpeg + Node.js (needed for yt-dlp n challenge)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    nodejs \
    npm \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Always install latest yt-dlp
RUN pip install --no-cache-dir --upgrade yt-dlp

COPY . .

EXPOSE 5000

CMD ["python", "app.py"]
