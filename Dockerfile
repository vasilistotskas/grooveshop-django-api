ARG PYTHON_VERSION=3.13.2
ARG UID=1000
ARG GID=1000
ARG APP_DIR=/home/app

FROM python:${PYTHON_VERSION}-alpine AS base
ARG UID
ARG GID
ARG APP_DIR

RUN apk update && \
    apk add --no-cache gcc musl-dev

RUN pip install --upgrade pip

RUN addgroup -g ${GID} app && \
    adduser -D -u ${UID} -G app app && \
    mkdir -p ${APP_DIR} && \
    chown -R app:app ${APP_DIR}

WORKDIR ${APP_DIR}

FROM base AS prod

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY --chown=app:app ./requirements.txt .

RUN pip install -r requirements.txt
RUN pip wheel --no-cache-dir --no-deps --wheel-dir /usr/src/app/wheels -r requirements.txt

COPY --chown=app:app . .

RUN mkdir -p ${APP_DIR}/web/staticfiles ${APP_DIR}/web/mediafiles && \
    chown -R app:app ${APP_DIR}/web/staticfiles ${APP_DIR}/web/mediafiles

VOLUME ${APP_DIR}/web/staticfiles
VOLUME ${APP_DIR}/web/mediafiles

USER app
