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
COPY web ./web
COPY migrations ./migrations
COPY scripts ./scripts
COPY alembic.ini ./

RUN mkdir -p /data && chown -R app:app /app /data
USER app

ENV NETCFG_DATABASE_URL=sqlite:////data/netcfg.db \
    NETCFG_COOKIE_SECURE=false

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=2).status==200 else 1)"

# Migrate first, then serve. Never runs as root.
CMD ["sh", "-c", "python -m alembic upgrade head && python -m uvicorn api.main:app --host 0.0.0.0 --port 8000"]
