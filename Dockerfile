# ---- UI build ----
FROM node:22-slim AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build   # vite outDir is ../web, so assets land in /web

# ---- runtime ----
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN adduser --disabled-password --gecos "" app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY api ./api
COPY domain ./domain
COPY engine ./engine
COPY migrations ./migrations
COPY scripts ./scripts
COPY alembic.ini ./
COPY --from=ui /web ./web

RUN mkdir -p /data && chown -R app:app /app /data
USER app

ENV NETCFG_DATABASE_URL=sqlite:////data/netcfg.db \
    NETCFG_COOKIE_SECURE=false

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=2).status==200 else 1)"

CMD ["sh", "-c", "python -m alembic upgrade head && python -m uvicorn api.main:app --host 0.0.0.0 --port 8000"]
