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
# The engine rides along: a few handlers parse their request with a function
# from there (`account.check_profile`), and without it those commands would
# reach the model with no arguments named.
COPY backend/src/api/commands/ /app/reference/commands/
COPY backend/src/engine/ /app/reference/engine/
ENV APS_SESSION_SOURCE=/app/reference/commands

# Agents, their journal and the provider key live in SQLite on a volume.
ENV APS_DB=/data/aps.sqlite3
VOLUME /data

ENV APS_HOST=0.0.0.0
ENV APS_PORT=8100
EXPOSE 8100
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3     CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8100/health').read()"
CMD ["python", "-m", "aps"]
