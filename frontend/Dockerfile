# ---- build stage: compile the React + TypeScript dashboard ----
FROM node:20-slim AS build

WORKDIR /app

# Baked into the bundle at build time. Override with:
#   docker compose build --build-arg VITE_API_URL=https://api.example.com
ARG VITE_API_URL=http://localhost:8000
ENV VITE_API_URL=${VITE_API_URL}

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

# ---- serve stage: static files behind nginx ----
FROM nginx:alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
