FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv
ARG INSTALL_DEV=0
COPY requirements.txt requirements-dev.txt ./
RUN if [ "$INSTALL_DEV" = "1" ]; then pip install -r requirements-dev.txt; else pip install -r requirements.txt; fi
COPY . .
RUN chmod +x docker/entrypoint.sh && useradd --system --uid 10001 app && chown -R app /srv
USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=2).status == 200 else 1)"
ENTRYPOINT ["docker/entrypoint.sh"]
