ARG PYTHON_VERSION=3.14.0
ARG ALPINE_VERSION=3.21
ARG UV_VERSION=0.9.0
ARG UV_IMAGE=ghcr.io/astral-sh/uv:${UV_VERSION}
ARG UID=1000
ARG GID=1000
ARG APP_PATH=/home/app

FROM $UV_IMAGE AS uv

FROM python:${PYTHON_VERSION}-alpine${ALPINE_VERSION} AS builder
ARG UV_IMAGE
ARG APP_PATH

RUN apk add --no-cache gcc musl-dev python3-dev linux-headers

COPY --from=uv /uv /uvx /bin/
WORKDIR ${APP_PATH}
COPY pyproject.toml .
COPY uv.lock .

RUN mkdir -p ${APP_PATH}/staticfiles ${APP_PATH}/mediafiles && \
    uv sync --frozen --no-install-project --no-editable

COPY . .
RUN uv sync --frozen --no-editable
ENTRYPOINT []

FROM python:${PYTHON_VERSION}-alpine${ALPINE_VERSION} AS production
ARG UID
ARG GID
ARG APP_PATH

RUN apk add --no-cache \
    postgresql-17 \
    postgresql-client-17 \
    gzip \
    && addgroup -g ${GID} -S app \
    && adduser -u ${UID} -S app -G app \
    && mkdir -p ${APP_PATH} \
    && chown app:app ${APP_PATH}

USER app
WORKDIR ${APP_PATH}
COPY --from=builder --chown=app:app ${APP_PATH} .

RUN mkdir -p ${APP_PATH}/staticfiles ${APP_PATH}/mediafiles ${APP_PATH}/backups \
    && chown -R app:app ${APP_PATH}/staticfiles ${APP_PATH}/mediafiles ${APP_PATH}/backups

CMD [".venv/bin/python", "manage.py", "runserver", "0.0.0.0:8000"]
