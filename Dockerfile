FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 SCRAPER_HEADLESS=true WEB_HOST=0.0.0.0 WEB_PORT=8000 PLAYWRIGHT_BROWSERS_PATH=/opt/browsers APP_ENV_FILE=/app/config/.env
COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir -r requirements.txt -c constraints.txt && python -m playwright install --with-deps chromium
RUN groupadd --gid 10001 avito && useradd --uid 10001 --gid avito --create-home avito
COPY --chown=avito:avito . .
RUN mkdir -p data exports logs config && chown -R avito:avito data exports logs config /opt/browsers
USER avito
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=3)"
CMD ["python", "web_server.py"]
