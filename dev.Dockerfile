ARG PYTHON_VERSION=3.13.2
ARG UID=1000
ARG GID=1000
ARG APP_DIR=/home/app

FROM python:${PYTHON_VERSION}-slim-bookworm AS base
ARG UID
ARG GID
ARG APP_DIR

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      gcc \
      libpq-dev \
      build-essential \
      git && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip

RUN groupadd -g ${GID} app \
    && useradd -m -u ${UID} -g app app \
    && mkdir -p ${APP_DIR} \
    && chown -R app:app ${APP_DIR}

WORKDIR ${APP_DIR}

FROM base AS dev

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEBUG=1

COPY --chown=app:app ./requirements.txt .
RUN pip install -r requirements.txt

COPY --chown=app:app . .

RUN mkdir -p ${APP_DIR}/staticfiles ${APP_DIR}/mediafiles \
    && chown -R app:app ${APP_DIR}/staticfiles ${APP_DIR}/mediafiles

USER app
