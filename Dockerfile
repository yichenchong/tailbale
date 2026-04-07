# --- Stage 1: Build frontend ---
FROM node:22-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- Stage 2: Production image ---
FROM python:3.12-slim AS production
WORKDIR /app

# Install lego (ACME client) for certificate management
ARG LEGO_VERSION=4.21.0
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL "https://github.com/go-acme/lego/releases/download/v${LEGO_VERSION}/lego_v${LEGO_VERSION}_linux_amd64.tar.gz" \
       | tar -xz -C /usr/local/bin lego \
    && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend code
COPY backend/app ./app

# Copy built frontend into static directory
COPY --from=frontend-build /app/frontend/dist ./static

# Bundle edge image build context so orchestrator can build it via Docker API
COPY edge/ ./edge-image/

# Data volume for db, secrets, certs, generated configs, tailscale state
VOLUME /data
ENV DATA_DIR=/data
ENV PORT=8080

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://localhost:{os.environ.get(\"PORT\",8080)}/api/health')"

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
