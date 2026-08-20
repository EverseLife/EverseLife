# The server image. The build context is **the repository root**, not `backend/`:
# together with the code the vault snapshot (`vault/`) goes into the image,
# otherwise the engine has nothing to compute the world with.
#
#   docker build -f backend/Dockerfile -t octoverse-backend .
#
# One image for three roles -- API, worker, migrations: they differ only by
# command, and they may not diverge in version.

FROM python:3.12-slim-bookworm AS builder

# uv installs dependencies by the lock (`uv.lock`) -- reproducibly and fast.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# The dependency layer separate from the code: an engine edit does not rebuild the environment.
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project


FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    # The vault build directory. The engine never reads the vault's Markdown.
    OCTOVERSE_VAULT_BUILD=/vault

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv

COPY backend/src ./src
COPY backend/migrations ./migrations
COPY backend/alembic.ini ./alembic.ini
# The numbers are not in the repository: build them before the image --
# `python deploy/sync-vault.py` locally, `.github/actions/vault` in CI.
COPY vault /vault

# The server has no reason to run as root: it reads the vault and talks to the database.
RUN useradd --system --create-home --uid 10001 octoverse \
    && chown -R octoverse:octoverse /app
USER octoverse

EXPOSE 8000

# Liveness check without curl: it is not in the image and there is no reason to add it.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health').read()"

# `--proxy-headers` -- a reverse proxy (Caddy) stands behind, and the player's
# real address arrives in a header.

CMD ["uvicorn", "src.api.app:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
