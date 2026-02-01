FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

RUN mkdir -p /app/logs /app/data

ENV TUBEARCHIVIST_DIR=/youtube
ENV NAVIDROME_DIR=/music
ENV DB_FILE=/app/data/mp3_downloaded.db
ENV TA_API_URL=http://tubearchivist.internal
ENV TA_TOKEN=your_token_here

ENTRYPOINT ["python", "main.py"]
