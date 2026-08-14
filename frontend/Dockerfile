# Образ клиента: сборка Vite и отдача статики nginx.
# Контекст — `frontend/`:
#
#   docker build -t octoverse-frontend frontend
#
# Адрес сервера в образ не зашит: собранный клиент ходит на тот же источник,
# откуда открыт (`/api`), — см. `src/api.ts`. Поэтому один образ годится и для
# боевого домена, и для пробного.

FROM node:24-alpine AS builder

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build


FROM nginx:1.27-alpine AS runtime

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80
