# Лендинг everse.life

Страница с приёмом заявок на бету: игрок оставляет почту, мы шлём одно письмо,
когда бета откроется. Одна служба на весь домен — `app.py` отдаёт `index.html`
и принимает `POST /api/signup`, заявки ложатся в SQLite (`/data/signups.db`,
том `landing_data` боевого состава). В базу игры лендинг не ходит.

От роботов — скрытое поле-приманка и ограничение частоты с адреса; повторная
заявка молча схлопывается (`UNIQUE` по почте), и наружу это не видно — чужой
почтой нельзя проверить, подписан ли её хозяин.

## SEO

Канонический адрес — `https://everse.life/` (константа `SITE` в `app.py`). Служба отдаёт
`/robots.txt`, `/sitemap.xml` (одна запись, `lastmod` по дате `index.html`) и
`/og.png` — карточку 1200×630 для соцсетей. В `index.html` зашиты Open Graph,
Twitter-карточка и JSON-LD (VideoGame + FAQPage — вопросы берутся из видимого FAQ,
при правке FAQ обновить и разметку). При смене домена правятся `SITE`
и абсолютные адреса в `<head>`.

Аналитика — Google Analytics 4 напрямую (`gtag.js`, ресурс `G-TZ5XDN557L`), без Tag
Manager: на одну страницу контейнер — лишний слой. Кроме `page_view` страница
шлёт два события: `signup_success` (успешная заявка — главная конверсия) и
`discord_click` (клик по любой ссылке на Discord). Вызовы защищены `if (window.gtag)`:
блокировщик рекламы вырезает счётчик, но форма и ссылки продолжают работать.
В GA4 `signup_success` надо один раз пометить ключевым событием (Администратор →
Ключевые события), иначе оно останется обычным событием в отчётах.

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
(`everselife-landing`). Домен включается на сервере один раз:

1. A-запись `everse.life` → адрес сервера;
2. `LANDING_DOMAIN=everse.life` в `/opt/everselife/.env`;
3. `docker compose up -d --force-recreate caddy`.

Пока `LANDING_DOMAIN` не задан, Caddy держит лендинг на внутреннем имени
`landing.localhost` и наружу не отдаёт.

## Забрать заявки

```bash
docker compose exec landing python export.py > signups.csv
```
