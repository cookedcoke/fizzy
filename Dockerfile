FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 FIZZY_DB=/data/fizzy.db
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY fizzy ./fizzy
RUN useradd -r -u 10001 fizzy && mkdir -p /data /backups && chown fizzy /data /backups
COPY deploy/entrypoint.sh /usr/local/bin/fizzy-entrypoint
# Starts as root only to fix ownership of the mounted folders, then runs as the fizzy user.
ENTRYPOINT ["fizzy-entrypoint"]
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
# One worker: SQLite + the in-process Square poller expect a single process.
CMD ["uvicorn", "fizzy.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
