# Образ сервера. Контекст сборки — **корень репозитория**, а не `backend/`:
# вместе с кодом в образ едет слепок вольта (`vault/`), иначе движку не с чем
# считать мир.
#
#   docker build -f backend/Dockerfile -t octoverse-backend .
#
# Один образ на три роли — API, воркер, миграции: они отличаются только
# командой, а расходиться версиями им нельзя.

FROM python:3.12-slim-bookworm AS builder

# uv ставит зависимости по замку (`uv.lock`) — воспроизводимо и быстро.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Слой зависимостей отдельно от кода: правка движка не пересобирает окружение.
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project


FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    # Каталог сборки вольта. Markdown вольта движок не читает никогда.
    OCTOVERSE_VAULT_BUILD=/vault

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv

COPY backend/src ./src
COPY backend/migrations ./migrations
COPY backend/alembic.ini ./alembic.ini
COPY vault /vault

# Сервер не имеет причин ходить под root: он читает вольт и говорит с базой.
RUN useradd --system --create-home --uid 10001 octoverse \
    && chown -R octoverse:octoverse /app
USER octoverse

EXPOSE 8000

# Проверка живости без curl: в образе его нет и заводить незачем.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health').read()"

# `--proxy-headers` — за спиной обратный проксик (Caddy), и настоящий адрес
# игрока приезжает заголовком.
CMD ["uvicorn", "src.api.app:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
