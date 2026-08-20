# ==============================================================================
# DOCKERFILE FOR AVITO MAX PARSER & AI DEAL SCORING ENGINE
# ==============================================================================
FROM mcr.microsoft.com/playwright/python:v1.45.0-jammy

WORKDIR /app

# Environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SCRAPER_HEADLESS=true \
    WEB_HOST=0.0.0.0 \
    WEB_PORT=8000

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    python -m playwright install chromium

# Copy application source
COPY . .

# Create persistent folders
RUN mkdir -p data exports logs

# Expose Web Interface Port
EXPOSE 8000

# Volume mounts for persistence
VOLUME ["/app/data", "/app/exports", "/app/logs"]

# Default start command: Web Server
CMD ["python", "web_server.py"]
