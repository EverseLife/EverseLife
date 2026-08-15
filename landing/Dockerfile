# The octoverse.world landing: the page and beta signup intake.
FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py export.py index.html favicon.svg ./

# Signups live in SQLite on a volume: the container is disposable, the database is not.
ENV LANDING_DB=/data/signups.db
VOLUME /data

EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
