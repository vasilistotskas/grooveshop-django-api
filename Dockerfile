ARG PYTHON_VERSION=3.15.2
ARG ALPINE_VERSION=3.24
ARG UV_VERSION=0.13.7
# PostgreSQL major of the CLIENT tools baked into the runtime image.
# pg_dump refuses to dump a server NEWER than itself ("aborting because
# of server version mismatch"), so this must track the server major:
# prod is grooveshop-infrastructure manifests/app-constructs/grooveshop/
# base/backend-database.yaml ``postgresql.version``; local dev is
# infra.compose.yml. It drifted to 17 after the PG18 upgrade and the
# nightly backup_database_task failed every night until 2026-09-02.
ARG POSTGRES_MAJOR=18
ARG UV_IMAGE=ghcr.io/astral-sh/uv:${UV_VERSION}
ARG UID=1000
ARG GID=1000
ARG APP_PATH=/home/app

FROM $UV_IMAGE AS uv

# Build Tailwind CSS
FROM node:26.13.0-alpine AS tailwind-builder
ARG APP_PATH
WORKDIR ${APP_PATH}
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM python:${PYTHON_VERSION}-alpine${ALPINE_VERSION} AS builder
ARG UV_IMAGE
ARG APP_PATH

# The image's own interpreter, in both stages. Without this, a
# .python-version that differs from PYTHON_VERSION makes uv download its
# own Python into /root, which the production stage does not copy: the
# image builds and its venv has no interpreter. With it, such a mismatch
# fails the build.
ENV UV_PYTHON_DOWNLOADS=0

# A compiler for the few dependencies that ship only an sdist for this
# Python (PyYAML, djangorestframework-camel-case), and gettext for
# compilemessages. WeasyPrint needs no headers: it is pure Python and
# loads its native libraries at run time with cffi's dlopen.
RUN apk add --no-cache gcc musl-dev linux-headers gettext

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
ARG POSTGRES_MAJOR

# Only the client tools: backup_database shells out to pg_dump (and
# gzip for --compress). psycopg[binary] bundles its own libpq, and the
# server package that used to sit next to the client was never used.
RUN apk add --no-cache \
    postgresql${POSTGRES_MAJOR}-client \
    gzip \
    # The libraries WeasyPrint dlopens at PDF-generation time
    # (weasyprint/text/ffi.py): pango brings gobject, harfbuzz,
    # fontconfig and pangoft2; harfbuzz-subset embeds font subsets, and
    # WeasyPrint warns at import without it.
    # Pango needs actual font files at runtime (without them you get
    # ``pango_font_describe: font != NULL`` criticals and blank PDFs);
    # ttf-dejavu covers Latin + Greek, font-noto-cjk covers CJK in case
    # a product name ever sneaks a Chinese character into an invoice.
    pango harfbuzz-subset \
    ttf-dejavu font-noto font-noto-cjk \
    fontconfig \
    && addgroup -g ${GID} -S app \
    && adduser -u ${UID} -S app -G app \
    && mkdir -p ${APP_PATH} \
    && chown app:app ${APP_PATH}

# Write stdout as it is printed. Off a terminal Python buffers it in blocks,
# so what is printed rather than logged (celery beat's start banner, a
# management command's output) reached the logs only when the process exited,
# and never if it was killed. Logging flushes every record.
ENV PYTHONUNBUFFERED=1

USER app
WORKDIR ${APP_PATH}
COPY --from=builder --chown=app:app ${APP_PATH} .

RUN mkdir -p ${APP_PATH}/staticfiles ${APP_PATH}/mediafiles ${APP_PATH}/backups \
    && chown -R app:app ${APP_PATH}/staticfiles ${APP_PATH}/mediafiles ${APP_PATH}/backups

# Multi-process ASGI serving: gunicorn master + uvicorn workers (see
# gunicorn.conf.py). Replaced single-process daphne, whose lone event
# loop stalled all requests — including K8s health probes — under any
# CPU-bound work (prod incident 2026-07-02).
CMD [".venv/bin/gunicorn", "-c", "gunicorn.conf.py", "asgi:application"]
