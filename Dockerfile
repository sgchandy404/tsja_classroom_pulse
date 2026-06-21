FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY src/ ./src/

# /data is the mount point for the SQLite volume (see docker-compose.yml)
RUN mkdir -p /data

EXPOSE 8000

WORKDIR /app/src
CMD ["gunicorn", "--workers", "2", "--bind", "0.0.0.0:8000", "--access-logfile", "-", "--error-logfile", "-", "app:app"]
