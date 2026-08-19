# Редактор рецептов в контейнере.
#
# Вольт внутрь образа НЕ кладётся и класть его нельзя: редактор существует ради
# того, чтобы менять файл, а копия в образе — это правки, которые никто не
# увидит. Вольт подключается томом:
#
#   docker build -t octoverse-vault-editor vault_editor
#   docker run --rm -p 8765:8765 \
#       -v /path/to/octoverse-game-design:/vault \
#       -v /path/to/octoverse/vault:/repo/vault \
#       -v /path/to/octoverse/vault_editor/backups:/backups \
#       octoverse-vault-editor
#
# Проще — `docker compose -f vault_editor/compose.yaml up`, там всё это уже
# описано.

FROM python:3.12-slim

# Единственная зависимость — та же, что у сборки вольта. Её же требует
# `tools/build.py`, который редактор запускает кнопками «Проверить» и «Собрать».
RUN pip install --no-cache-dir pyyaml==6.0.2

WORKDIR /app
COPY . /app

ENV OCTOVERSE_VAULT=/vault \
    OCTOVERSE_REPO=/repo \
    OCTOVERSE_EDITOR_BACKUPS=/backups \
    OCTOVERSE_EDITOR_HOST=0.0.0.0 \
    PYTHONUTF8=1 \
    PYTHONIOENCODING=utf-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Иначе `docker logs` пуст до заполнения буфера, и запуск выглядит немым.
    PYTHONUNBUFFERED=1

EXPOSE 8765

# Проверка живости заодно проверяет и главное: что вольт подключён и читается.
# Без тома контейнер поднимется, но покажет пустой экран — пусть лучше он сразу
# считается нездоровым.
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/api/state', timeout=4).status == 200 else 1)"

CMD ["python", "server.py", "--no-browser"]
