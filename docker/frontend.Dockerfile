# VaultX admin UI (React/Vite) + nginx die ook de backend proxyt.
#   docker build -f docker/frontend.Dockerfile -t vaultx-web .
FROM node:22-alpine AS build
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.29-alpine
COPY docker/nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /src/dist /usr/share/nginx/html
ENV VAULTX_BACKEND=backend:8000
EXPOSE 8080
