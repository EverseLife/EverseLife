# The agents panel and runner (D-224). Built from the repository root: the
# protocol reference is read from the server's session.py, so the image needs
# that one file next to the package.
#   docker build -f agentic_player_system/Dockerfile .
FROM python:3.12-slim

WORKDIR /app

COPY agentic_player_system/pyproject.toml ./
COPY agentic_player_system/aps/ ./aps/
RUN pip install --no-cache-dir .

# The client protocol reference, frozen at build time with the same revision.
COPY backend/src/api/session.py /app/reference/session.py
ENV APS_SESSION_SOURCE=/app/reference/session.py

# Agents, their journal and the provider key live in SQLite on a volume.
ENV APS_DB=/data/aps.sqlite3
VOLUME /data

ENV APS_HOST=0.0.0.0
ENV APS_PORT=8100
EXPOSE 8100
CMD ["python", "-m", "aps"]
