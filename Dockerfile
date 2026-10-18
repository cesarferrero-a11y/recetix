FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 user
USER user
ENV PATH="/home/user/.local/bin:$PATH"
WORKDIR /home/user/app

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=user . .

# Al arrancar se actualiza yt-dlp (TikTok cambia a menudo) y luego se lanza la app.
CMD ["sh", "-c", "pip install -U --no-cache-dir yt-dlp || true; uvicorn main:app --host 0.0.0.0 --port ${PORT:-7860}"]
