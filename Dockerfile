FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY src/ ./src/

# /data is the mount point for the SQLite volume (see docker-compose.yml)
RUN mkdir -p /data

EXPOSE 8000

# With WORKDIR=/app/src the relative imports (from auth import …) resolve
# correctly. PYTHONPATH=/app lets Gunicorn find the module as "src.app".
ENV PYTHONPATH=/app

WORKDIR /app/src
CMD ["gunicorn", "--workers", "2", "--bind", "0.0.0.0:8000", "--access-logfile", "-", "--error-logfile", "-", "src.app:app"]
