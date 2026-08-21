# The client image: Vite build and static serving by nginx.
# The context is `frontend/`:
#
#   docker build -t everselife-frontend frontend
#
# The server address is not baked into the image: the built client goes to the
# same origin it was opened from (`/api`) -- see `src/api.ts`. So one image
# suits both the production domain and a trial one.


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
