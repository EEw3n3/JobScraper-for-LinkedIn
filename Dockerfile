FROM python:3.13-slim-bookworm

ARG APP_UID=1000
ARG APP_GID=1000

# Install browser and driver together from the same Debian repositories.
RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium chromium-driver ca-certificates fonts-liberation openbox novnc websockify x11vnc xvfb \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "${APP_GID}" jobparser \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --create-home jobparser

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CHROME_BINARY=/usr/bin/chromium \
    CHROMEDRIVER_PATH=/usr/bin/chromedriver \
    JOBPARSER_HEADLESS=1 \
    JOBPARSER_CHROME_NO_SANDBOX=1 \
    JOBPARSER_RESULTS_DIR=/app/results \
    JOBPARSER_CV_DIR=/app/cv_storage \
    JOBPARSER_STATE_DIR=/app/web_state \
    JOBPARSER_WEB_HOST=0.0.0.0 \
    JOBPARSER_WEB_PORT=8080 \
    JOBPARSER_WEB_USER=admin

WORKDIR /app
COPY requirements.txt requirements-ai.txt requirements-web.txt ./
RUN pip install --no-cache-dir -r requirements-web.txt
COPY main.py run_analysis.py webapp.py docker-entrypoint.sh ./
COPY web/ ./web/
COPY config/ ./config/
COPY scraper/ ./scraper/
COPY utils/ ./utils/
RUN mkdir -p /app/results /app/cv_storage /app/web_state \
    && chown "${APP_UID}:${APP_GID}" /app/results /app/cv_storage /app/web_state \
    && chmod 755 /app/docker-entrypoint.sh

USER ${APP_UID}:${APP_GID}
EXPOSE 8080 6080
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3).close()"]
ENTRYPOINT ["/app/docker-entrypoint.sh"]
