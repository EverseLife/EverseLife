# The everse.life landing: the page and beta signup intake.
FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py export.py indexnow.py lastmod.py lastmod.json ./
# Every page, not a list of them: a page added to `SITE_PAGES` and forgotten
# here passes the tests -- they read the source tree -- and is missing only in
# the image, where it takes the sitemap down with it. The folders under /en/
# mirror the URLs.
COPY *.html ./
COPY en/ ./en/
COPY site.css site.js space.js favicon.svg og.png og-en.png ./
COPY fonts/ ./fonts/

# Signups live in SQLite on a volume: the container is disposable, the database is not.
ENV LANDING_DB=/data/signups.db
VOLUME /data

EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
