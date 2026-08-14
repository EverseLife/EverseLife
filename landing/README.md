# Лендинг octoverse.world

Страница с приёмом заявок на бету: игрок оставляет почту, мы шлём одно письмо,
когда бета откроется. Одна служба на весь домен — `app.py` отдаёт `index.html`
и принимает `POST /api/signup`, заявки ложатся в SQLite (`/data/signups.db`,
том `landing_data` боевого состава). В базу игры лендинг не ходит.

От роботов — скрытое поле-приманка и ограничение частоты с адреса; повторная
заявка молча схлопывается (`UNIQUE` по почте), и наружу это не видно — чужой
почтой нельзя проверить, подписан ли её хозяин.

## Локально

```bash
pip install -r requirements.txt
LANDING_DB=./signups.db uvicorn app:app --port 8080
```

## В бою

Служба `landing` в `deploy/compose.yaml`, образ собирает CI
(`octoverse-landing`). Домен включается на сервере один раз:

1. A-запись `octoverse.world` → адрес сервера;
2. `LANDING_DOMAIN=octoverse.world` в `/opt/octoverse/.env`;
3. `docker compose up -d --force-recreate caddy`.

Пока `LANDING_DOMAIN` не задан, Caddy держит лендинг на внутреннем имени
`landing.localhost` и наружу не отдаёт.

## Забрать заявки

```bash
docker compose exec landing python export.py > signups.csv
```
