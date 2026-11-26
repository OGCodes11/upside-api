# The public Upside API: derived data only, served from the read-only snapshot data/public.db.
# Build on a Mac for Render's servers:  docker build --platform linux/amd64 -t upside-api .
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PUBLIC_MODE=1 \
    UPSIDE_DB_PATH=/srv/public.db \
    PORT=10000

WORKDIR /app
COPY requirements-api.txt .
RUN pip install --no-cache-dir -r requirements-api.txt

COPY app/ app/
COPY scoring/ scoring/
# Owned by root and read-only: the server's user can read the snapshot but never change it.
COPY --chmod=444 data/public.db /srv/public.db

RUN useradd --system --no-create-home upside
USER upside

EXPOSE 10000
# sh -c so ${PORT} (set by Render) is expanded; exec so uvicorn, not the shell, gets stop signals.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
