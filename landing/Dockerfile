# Лендинг octoverse.world: страница и приём заявок на бету.
FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py export.py index.html favicon.svg ./

# Заявки живут в SQLite на томе: контейнер одноразовый, база — нет.
ENV LANDING_DB=/data/signups.db
VOLUME /data

EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
