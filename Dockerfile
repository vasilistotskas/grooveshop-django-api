ARG PYTHON_VERSION=3.13.2
ARG ALPINE_VERSION=3.21
ARG UV_VERSION=0.7.5
ARG UV_IMAGE=ghcr.io/astral-sh/uv:${UV_VERSION}
ARG UID=1000
ARG GID=1000
ARG APP_PATH=/home/app

FROM $UV_IMAGE AS uv

FROM python:${PYTHON_VERSION}-alpine${ALPINE_VERSION} AS builder
ARG UV_IMAGE
ARG APP_PATH
COPY --from=uv /uv /uvx /bin/
WORKDIR ${APP_PATH}
COPY pyproject.toml .
COPY uv.lock .

RUN mkdir -p ${APP_PATH}/staticfiles ${APP_PATH}/mediafiles

RUN uv sync --frozen --no-install-project --no-editable
ADD . .
RUN uv sync --frozen --no-editable
ENTRYPOINT []

FROM python:${PYTHON_VERSION}-alpine${ALPINE_VERSION} AS production
ARG UID
ARG GID
ARG APP_PATH
RUN addgroup -g ${GID} -S app && adduser -u ${UID} -S app -G app
RUN mkdir -p ${APP_PATH} && chown app:app ${APP_PATH}
USER app
WORKDIR ${APP_PATH}
COPY --from=builder --chown=app:app ${APP_PATH} .

RUN mkdir -p ${APP_PATH}/staticfiles ${APP_PATH}/mediafiles && \
    chown -R app:app ${APP_PATH}/staticfiles ${APP_PATH}/mediafiles

CMD [".venv/bin/python", "manage.py", "runserver", "0.0.0.0:8000"]

FROM production AS production_cicd
USER root

RUN apk add --no-cache \
    docker-cli \
    docker-compose \
    git

USER app
