# ---- build stage: compile the React + TypeScript dashboard ----
FROM node:20-slim AS build

WORKDIR /app

# Baked into the bundle at build time. Empty (the default) means
# "same origin": the dashboard calls the relative path /api/*, which the
# bundled nginx reverse-proxies to the backend with the API key attached
# server-side (see nginx.conf.template and docs/AUTH.md). Override only to
# point the dashboard at a remote backend directly:
#   docker compose build --build-arg VITE_API_URL=https://api.example.com
ARG VITE_API_URL=
ENV VITE_API_URL=${VITE_API_URL}

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

# ---- serve stage: static files behind nginx ----
FROM nginx:alpine

# Rendered by the image entrypoint at startup (envsubst): ${WAMSS_API_KEY}
# becomes the key the /api/ proxy attaches server-side. The browser never
# sees it. Pass the key with e.g. docker-compose.yml `environment:`.
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
