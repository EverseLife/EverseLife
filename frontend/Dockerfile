# The client image: Vite build and static serving by nginx.
# The context is `frontend/`:
#
#   docker build -t everselife-frontend \
#     --build-arg RELEASE=$(git rev-parse HEAD) frontend
#
# The server address is not baked into the image: the built client goes to the
# same origin it was opened from (`/api`) -- see `src/api.ts`. So one image
# suits both the production domain and a trial one.


FROM node:24-alpine AS builder

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# Which revision this client was built from. It ends up in the link to the
# source in the header (§13 AGPL, `src/App.tsx`). Vite bakes `VITE_*` in at
# build time, so it is an argument of the build and not a setting of the
# running container -- nginx serves files, it has nowhere to read one from.
# Declared here and not higher up: `npm ci` above must stay cached.
ARG RELEASE=""
ENV VITE_RELEASE=${RELEASE}

RUN npm run build


FROM nginx:1.27-alpine AS runtime

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80
