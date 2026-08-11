# syntax=docker/dockerfile:1

FROM python:3.12.13-slim-bookworm

ARG APP_VERSION=v0.3.0

LABEL org.opencontainers.image.title="opslab-api" \
      org.opencontainers.image.description="FastAPI service for the opslab-k8s SRE practice project" \
      org.opencontainers.image.version="${APP_VERSION}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_NAME=opslab-api \
    APP_VERSION=${APP_VERSION} \
    APP_ENV=development

WORKDIR /app

RUN groupadd --gid 10001 appuser \
    && useradd \
       --uid 10001 \
       --gid 10001 \
       --no-create-home \
       --home-dir /app \
       --shell /usr/sbin/nologin \
       appuser

COPY requirements.lock.txt /tmp/requirements.lock.txt

RUN python -m pip install \
      --no-cache-dir \
      --requirement /tmp/requirements.lock.txt \
    && rm -f /tmp/requirements.lock.txt

COPY --chown=10001:10001 app/ ./app/

USER 10001:10001

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host=0.0.0.0", "--port=8000"]
