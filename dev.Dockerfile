ARG PYTHON_VERSION=3.13.2
ARG UV_VERSION=0.7.8
ARG UV_IMAGE=ghcr.io/astral-sh/uv:${UV_VERSION}

FROM $UV_IMAGE AS uv

FROM python:${PYTHON_VERSION}-slim-bookworm AS base
ARG UID=1000
ARG GID=1000
ARG APP_PATH=/home/app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    HOME=${APP_PATH}

RUN apt-get update && apt-get install -y --no-install-recommends \
    && addgroup --system --gid ${GID} appgroup \
    && adduser --system \
         --uid    ${UID} \
         --gid    ${GID} \
         --home   ${APP_PATH} \
         --shell  /bin/bash \
         appuser \
    && mkdir -p ${APP_PATH}/.cache/uv ${APP_PATH}/staticfiles ${APP_PATH}/mediafiles \
    && chown -R appuser:appgroup ${APP_PATH} \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY --chown=appuser:appgroup --from=uv /uv /uvx /bin/

WORKDIR ${APP_PATH}

COPY --chown=appuser:appgroup pyproject.toml uv.lock ./

RUN --mount=type=cache,target=${APP_PATH}/.cache/uv,uid=${UID},gid=${GID} \
    uv sync --frozen --no-install-project --no-editable

COPY --chown=appuser:appgroup . .

RUN --mount=type=cache,target=${APP_PATH}/.cache/uv,uid=${UID},gid=${GID} \
    uv sync --frozen --no-editable

FROM base AS default

USER appuser

CMD ["uv", "run", "python", "manage.py", "runserver", "0.0.0.0:8000"]

FROM base AS cicd
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    docker-compose \
    docker.io \
    git \
    && apt-get clean && rm -rf /var/lib/apt/lists/* \

USER appuser
