# --- Stage 1: build the React app ---
FROM node:20-alpine AS web
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- Stage 2: Python API that also serves the built React app ---
FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ .
COPY --from=web /web/dist ./static

# Optional: pull real Elexon data at build time:
#   docker build --build-arg FETCH_DATA=1 -t wind-desk .
ARG FETCH_DATA=0
RUN if [ "$FETCH_DATA" = "1" ]; then python -m scripts.fetch_data; fi

ENV PORT=8000
EXPOSE 8000
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
