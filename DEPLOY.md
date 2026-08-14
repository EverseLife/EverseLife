# Выкладка альфы

Как поднять OctoVerse на сервере, выдать ему настоящий сертификат и настроить
выкладку с GitHub. Порядок написан для чистой Ubuntu 24.04 и домена вида
`alpha.example.com`, но на любом другом дистрибутиве с Docker всё то же самое.

## Из чего состоит боевой состав

`deploy/compose.yaml` поднимает семь служб:

| служба | что делает |
| --- | --- |
| `postgres` | база. Порты наружу не публикует: в неё ходят по внутренней сети состава |
| `migrate` | накатывает схему (`alembic upgrade head`) и завершается. Сервер стартует только после него |
| `backend` | сервер игры: чтение по `/public/*`, сессия игрока по `/session/ws` |
| `worker` | доводит долгие дела: тик мира, партии, дороги, счета за быт. **Без него мир стоит** |
| `frontend` | nginx с собранным клиентом |
| `landing` | лендинг с приёмом заявок на бету, свой домен (`LANDING_DOMAIN`). Заявки — SQLite на томе `landing_data`, в базу игры не ходит |
| `caddy` | граница: TLS, сертификат Let's Encrypt, маршруты `/` → клиент и `/api/*` → сервер; лендинг — отдельным доменом |

`backend`, `worker` и `migrate` — один и тот же образ: расходиться версиями им
нельзя. Внутрь образа зашит слепок вольта (`vault/*.json`) — числа игры едут
вместе с кодом, а какие именно, видно в `/api/health` отпечатком констант.

Клиент собран без боевого домена внутри: он ходит на тот же источник, откуда
открыт, на путь `/api`. Один образ годится любому домену.

## Что нужно

* сервер: 2 ядра, 4 ГБ памяти, 20 ГБ диска — хватит на альфу с запасом;
* домен и A-запись на адрес сервера (`alpha.example.com` → `203.0.113.10`);
* открытые снаружи порты **80 и 443**. Порт 80 нужен не для игры, а для выдачи
  сертификата: без него Let's Encrypt не подтвердит домен;
* Docker с плагином compose.

## Один раз: подготовка сервера

```bash
# от root
apt update && apt upgrade -y
curl -fsSL https://get.docker.com | sh

# отдельный человек для игры, чтобы не жить под root
adduser --disabled-password --gecos "" octoverse
usermod -aG docker octoverse

# Пароля у него нет, значит нужен ключ — иначе к нему не подключиться.
# Проще всего отдать тот же, которым вы ходите root'ом.
install -d -m 700 -o octoverse -g octoverse /home/octoverse/.ssh
cp /root/.ssh/authorized_keys /home/octoverse/.ssh/authorized_keys
chown octoverse:octoverse /home/octoverse/.ssh/authorized_keys
chmod 600 /home/octoverse/.ssh/authorized_keys

mkdir -p /opt/octoverse
chown octoverse:octoverse /opt/octoverse
```

Проверить, что дверь открылась, — со своей машины:

```powershell
ssh -i $env:USERPROFILE\.ssh\<ваш ключ> octoverse@alpha.example.com "docker version --format '{{.Server.Version}}'"
```

`scp` и `ssh` берут только ключи с именами по умолчанию (`id_ed25519`,
`id_rsa`), поэтому свой указывайте явно через `-i` — либо опишите сервер раз и
навсегда в `~/.ssh/config`:

```
Host octoverse
  HostName alpha.example.com
  User octoverse
  IdentityFile ~/.ssh/<ваш ключ>
```

Брандмауэр — только то, что нужно:

```bash
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
```

Дальше всё от пользователя `octoverse`.

Положить на сервер состав и настройки. Файлы `compose.yaml` и `Caddyfile`
лежат в репозитории (`deploy/`), и дальше их будет привозить выкладка; в первый
раз проще скопировать руками со своей машины:

```powershell
# из корня репозитория, на своей машине
scp deploy/compose.yaml deploy/Caddyfile deploy/.env.example octoverse@alpha.example.com:/opt/octoverse/
```

На сервере сделать из образца настоящий `.env`:

```bash
cd /opt/octoverse
mv .env.example .env
# придумать пароль базы
openssl rand -base64 32
nano .env      # DOMAIN, ACME_EMAIL, POSTGRES_PASSWORD, GHCR_OWNER
chmod 600 .env
```

`.env` живёт только на сервере и в репозиторий не попадает: в нём пароль базы.
Выкладка его не трогает.

## Один раз: образы

Образы собирает CI и кладёт в GitHub Container Registry. Чтобы сервер мог их
забрать, нужен доступ. Два пути:

1. **сделать пакеты открытыми** — GitHub → репозиторий → Packages →
   `octoverse-backend` → Package settings → Change visibility → Public. Тогда
   сервер тянет образы без пропуска, а выкладке ничего не нужно объяснять;
2. **входить по пропуску** — создать токен с правом `read:packages` и один раз
   выполнить на сервере `docker login ghcr.io -u <логин>`.

Выкладка из CI входит в реестр сама, своим одноразовым пропуском, — оба
варианта нужны только для запусков руками.

## Первый запуск

Сначала CI должен собрать образы: толкнуть ветку `main` и дождаться зелёного
прогона (`Actions` → `CI`). Дальше на сервере:

```bash
cd /opt/octoverse
docker compose pull
docker compose up -d
docker compose ps
```

Схему накатит служба `migrate` до старта сервера — руками ничего накатывать не
нужно. Мир при этом ещё пуст: столицу заводит сид, и его запускают **один раз**:

```bash
docker compose run --rm backend python -m src.seed
```

Повторный запуск сида ничего не портит, но и не нужен: мир вечный, вайпов не
бывает.

Проверка:

```bash
curl https://alpha.example.com/api/health
# {"ok":true,"constants":"<отпечаток>"}
```

Отпечаток в ответе — те самые числа вольта, на которых работает сервер. Открыть
`https://alpha.example.com` — должен подняться клиент и завестись сессия.

## SSL

Сертификатом занимается Caddy, и это весь ответ: он сам получает его у
Let's Encrypt по протоколу ACME и сам продлевает за месяц до конца срока.
Никакого certbot, никакого задания в cron, никакой перезагрузки по расписанию.

Что для этого должно совпасть:

1. `DOMAIN` в `.env` — тот самый домен, которым игроки открывают игру;
2. A-запись домена указывает на адрес сервера. Проверить: `dig +short
   alpha.example.com` должен вернуть адрес сервера;
3. порт **80** доступен снаружи. Через него идёт подтверждение владения
   доменом (HTTP-01). Закрыть его «потому что весь трафик по 443» — самая
   частая причина, по которой сертификат не выдаётся;
4. `ACME_EMAIL` — живая почта: туда придёт письмо, если с продлением беда.

Что происходит при первом запуске: Caddy видит домен в `Caddyfile`, идёт за
сертификатом, кладёт его в том `caddy_data` и включает HTTPS. Заодно он сам
отправляет всех с `http://` на `https://`. Том переживает пересоздание
контейнера — у Let's Encrypt есть предел на число выдач для одного домена
(пять штук в неделю), и терять уже выданное при каждой выкладке нельзя.

Посмотреть, как прошло:

```bash
docker compose logs caddy | tail -50
```

Строка `certificate obtained successfully` — всё получилось. Если вместо неё
ошибки подтверждения, разбираться по порядку: DNS доехал? порт 80 открыт
снаружи (`curl http://alpha.example.com` с другой машины)? домен в `.env`
совпадает с тем, что в DNS?

Пока настройка не устоялась, лучше пробовать на **испытательном** удостоверяющем
центре: его сертификату браузер не поверит, зато пределов на выдачу там
практически нет. Добавить в начало `deploy/Caddyfile`, в общий блок:

```
{
	email {$ACME_EMAIL}
	acme_ca https://acme-staging-v02.api.letsencrypt.org/directory
}
```

Как получилось — строку убрать, выполнить `docker compose up -d --force-recreate
caddy` и получить настоящий сертификат.

Проверить срок и цепочку снаружи:

```bash
echo | openssl s_client -connect alpha.example.com:443 -servername alpha.example.com 2>/dev/null | openssl x509 -noout -dates -issuer
```

### Если нужен именно nginx с certbot

Caddy заменяем, порядок такой: поставить `nginx` и `certbot` на сам сервер
(`apt install nginx certbot python3-certbot-nginx`), убрать службу `caddy` из
состава, опубликовать наружу порты клиента и сервера только на петлю
(`127.0.0.1:8080:80` у `frontend`, `127.0.0.1:8000:8000` у `backend`) и положить
в `/etc/nginx/sites-available/octoverse`:

```nginx
server {
    listen 80;
    server_name alpha.example.com;

    location /api/ {
        proxy_pass http://127.0.0.1:8000/;
        proxy_http_version 1.1;
        # Сессия игрока — сокет: без этих двух заголовков она не поднимется.
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # Игрок может молчать в сокет часами: короткий срок оборвёт сессию.
        proxy_read_timeout 3600s;
    }

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
    }
}
```

Дальше `ln -s` в `sites-enabled`, `nginx -t && systemctl reload nginx` и
`certbot --nginx -d alpha.example.com`: certbot сам перепишет блок под 443 и
заведёт продление таймером systemd (`systemctl list-timers | grep certbot`).
Проверить продление вхолостую: `certbot renew --dry-run`.

## CI/CD

Всё живёт в `.github/workflows/ci.yml` и идёт одной цепочкой:

1. **Сервер** — окружение по замку `uv.lock`, `ruff`, `alembic upgrade head` на
   чистой базе, полный `pytest` (~8 минут). База поднимается службой прогона;
2. **Клиент** — `npm ci`, `oxlint`, сборка;
3. **Образы** — только с `main`: собираются и уезжают в GHCR под двумя метками,
   `latest` и отпечаток коммита;
4. **Выкладка** — только с `main`: привозит на сервер `compose.yaml` и
   `Caddyfile`, тянет образы, поднимает состав и стучится в `/api/health`.

На запросах слияния идут первые два шага: на сервер уезжает только то, что
прошло тесты.

### Что завести на GitHub

Settings → Secrets and variables → Actions.

Секреты:

| имя | что это |
| --- | --- |
| `DEPLOY_HOST` | адрес или домен сервера |
| `DEPLOY_USER` | `octoverse` |
| `DEPLOY_SSH_KEY` | закрытый ключ выкладки целиком, вместе со строками `BEGIN`/`END` |
| `DEPLOY_PORT` | порт SSH, если не 22 (необязательно) |
| `DEPLOY_KNOWN_HOSTS` | строка из `ssh-keyscan` для сервера (необязательно, но лучше завести) |

Переменные (вкладка Variables):

| имя | значение |
| --- | --- |
| `DEPLOY_ENABLED` | `true` — пока её нет, выкладка молчит |
| `DEPLOY_PATH` | `/opt/octoverse` (необязательно) |
| `DEPLOY_DOMAIN` | `alpha.example.com` — по нему проверяется, что сервер ожил |

Ключ выкладки — отдельный, не тот, которым вы сами ходите на сервер:

```bash
# на своей машине
ssh-keygen -t ed25519 -f deploy_key -N "" -C "github-deploy"
ssh-keyscan alpha.example.com     # это в DEPLOY_KNOWN_HOSTS
```

`ssh-copy-id` в Windows нет, поэтому открытую половину доливаем строкой:

```powershell
type deploy_key.pub | ssh octoverse@alpha.example.com "cat >> ~/.ssh/authorized_keys"
```

Содержимое `deploy_key` (закрытая половина) — в `DEPLOY_SSH_KEY`, после чего
файл с машины стереть.

### Откат

Образы помечены отпечатком коммита, так что вернуться к прошлой сборке — дело
одной строки на сервере:

```bash
cd /opt/octoverse
sed -i 's/^TAG=.*/TAG=<отпечаток коммита>/' .env
docker compose pull && docker compose up -d
```

Схему это не откатывает: миграции вперёд, откат — отдельным решением.
Возвращаясь на `latest`, не забыть вернуть строку обратно.

## Лендинг

Лендинг (`landing/`) едет тем же путём, что и всё остальное: CI собирает образ
`octoverse-landing`, выкладка поднимает службу. Наружу он не смотрит, пока на
сервере не сделано один раз:

1. A-запись голого домена (`example.com`) → адрес сервера;
2. `LANDING_DOMAIN=example.com` в `/opt/octoverse/.env`;
3. `docker compose up -d --force-recreate caddy` — Caddy возьмёт сертификат.

Заявки на бету копятся в SQLite на томе `landing_data`. Забрать:

```bash
cd /opt/octoverse
docker compose exec landing python export.py > signups.csv
```

Подробности — в [landing/README.md](landing/README.md).

## Числа игры

Числа, рецепты и законы правятся **только в вольте**
(`octoverse-game-design/data/*.yaml`). Путь до сервера такой:

```powershell
# из корня репозитория игры, на своей машине
powershell -File deploy/sync-vault.ps1
git add vault && git commit -m "числа: ..." && git push
```

Дальше CI сам соберёт образ со свежими числами и выложит его. Править `vault/`
руками или подкладывать JSON на сервер — значит развести боевой мир с вольтом:
отпечаток в `/api/health` перестанет что-либо значить.

## Эксплуатация

```bash
cd /opt/octoverse

docker compose ps                       # кто жив
docker compose logs -f backend worker   # что происходит
docker compose restart worker           # мир встал — начинать отсюда
docker compose exec postgres psql -U octoverse -d octoverse
```

Снимок базы (мир вечный, вайпов не бывает — снимки не роскошь):

```bash
docker compose exec -T postgres pg_dump -U octoverse -Fc octoverse > ~/octoverse-$(date +%F).dump
```

Вернуть из снимка:

```bash
docker compose stop backend worker
docker compose exec -T postgres pg_restore -U octoverse -d octoverse --clean --if-exists < ~/octoverse-2026-08-14.dump
docker compose start backend worker
```

Ежедневный снимок в cron у пользователя `octoverse` — пять минут работы и
единственное, что отделяет альфу от потери мира:

```
0 4 * * * cd /opt/octoverse && docker compose exec -T postgres pg_dump -U octoverse -Fc octoverse > ~/backup/octoverse-$(date +\%F).dump && find ~/backup -name '*.dump' -mtime +14 -delete
```

## Что в альфе честно не сделано

* **Опознания нет.** Сессия открывается по имени: кто угодно может назваться кем
  угодно (заглушка до Э7). Пока это так, открытая альфа — это доверие всем, кто
  знает адрес. Закрытую разумно прикрыть дверью с паролем на границе: в
  `deploy/Caddyfile` для этого заготовлен блок `basic_auth`;
* **сокет не проверяет источник.** CORS закрывает чтение чужому домену, но на
  соединение сессии он не распространяется;
* **воркер один.** Их можно поднять сколько угодно (`docker compose up -d
  --scale worker=2`), задания разбираются без координации, но альфе хватит
  одного;
* **метрик наружу нет** — только журналы служб;
* **снимки базы не делаются сами**, пока не заведёте задание выше.
