# Лендинг octoverse.world

Страница с приёмом заявок на бету: игрок оставляет почту, мы шлём одно письмо,
когда бета откроется. Одна служба на весь домен — `app.py` отдаёт `index.html`
и принимает `POST /api/signup`, заявки ложатся в SQLite (`/data/signups.db`,
том `landing_data` боевого состава). В базу игры лендинг не ходит.

От роботов — скрытое поле-приманка и ограничение частоты с адреса; повторная
заявка молча схлопывается (`UNIQUE` по почте), и наружу это не видно — чужой
почтой нельзя проверить, подписан ли её хозяин.

## Шрифты

Те же три гарнитуры, что и в клиенте (D-075): Onest — интерфейс и заголовки,
IBM Plex Mono — числа и служебные подписи, Literata — голос мира. Все три под
OFL, лежат в `fonts/` подрезанными под латиницу и кириллицу (woff2, ~210 КБ на
пять файлов) и отдаются со своего домена маршрутом `/fonts/{name}` — сторонних
CDN нет. Лицензии `OFL-*.txt` лежат рядом, этого требует сама лицензия.

Пересобрать из исходников (`google/fonts`: `ofl/onest`, `ofl/ibmplexmono`,
`ofl/literata`):

```bash
pip install fonttools brotli
pyftsubset "Onest[wght].ttf" --output-file=fonts/onest.woff2 --flavor=woff2 --layout-features='*' \
  --unicodes="U+0000-00FF,U+0400-045F,U+2010-2027,U+2190-2199,U+20A0-20BF,U+2116,U+2212"
```

Literata перед подрезкой зафиксирована по оптическому размеру
(`fonttools varLib.instancer "Literata[opsz,wght].ttf" opsz=14 wght=400:700`),
иначе два файла весили бы по четверти мегабайта.

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
