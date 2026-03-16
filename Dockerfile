FROM python:3.11-slim

# ffmpeg/ffprobe — нарезка аудио на чанки + конвертация аудио из YouTube/Instagram (yt-dlp)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg nodejs && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot/ bot/

CMD ["python", "-m", "bot.main"]
