ARG PYTHON_VERSION=3.14.2
ARG ALPINE_VERSION=3.23
ARG UV_VERSION=0.11.6
ARG UV_IMAGE=ghcr.io/astral-sh/uv:${UV_VERSION}
ARG UID=1000
ARG GID=1000
ARG APP_PATH=/home/app

FROM $UV_IMAGE AS uv

# Build Tailwind CSS
FROM node:25-alpine AS tailwind-builder
ARG APP_PATH
WORKDIR ${APP_PATH}
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM python:${PYTHON_VERSION}-alpine${ALPINE_VERSION} AS builder
ARG UV_IMAGE
ARG APP_PATH

RUN apk add --no-cache gcc musl-dev python3-dev linux-headers gettext

COPY --from=uv /uv /uvx /bin/
WORKDIR ${APP_PATH}
COPY pyproject.toml .
COPY uv.lock .

RUN mkdir -p ${APP_PATH}/staticfiles ${APP_PATH}/mediafiles && \
    uv sync --frozen --no-install-project --no-editable --no-dev

COPY . .
# Copy pre-built Tailwind CSS from tailwind-builder stage
COPY --from=tailwind-builder ${APP_PATH}/static/css/styles.css ./static/css/styles.css
RUN uv sync --frozen --no-editable --no-dev

# Compile gettext .mo files from the committed .po sources. Kept in the
# builder stage only — the final image reads .mo at runtime via Python's
# stdlib gettext which has no msgfmt dependency. --ignore=.venv skips
# vendored third-party .po files that already ship pre-compiled.
RUN .venv/bin/python manage.py compilemessages --ignore=.venv
ENTRYPOINT []

FROM python:${PYTHON_VERSION}-alpine${ALPINE_VERSION} AS production
ARG UID
ARG GID
ARG APP_PATH

RUN apk add --no-cache \
    postgresql17 \
    postgresql17-client \
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

CMD [".venv/bin/daphne", "-b", "0.0.0.0", "-p", "8000", "asgi:application"]
