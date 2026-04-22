ARG PYTHON_VERSION=3.14.2
ARG UV_VERSION=0.11.6
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
    curl ca-certificates gnupg \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc --fail https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    && . /etc/os-release \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt $VERSION_CODENAME-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
    postgresql-client-17 \
    postgresql-17 \
    gzip \
    # gettext tools (msgfmt / xgettext) for `makemessages` +
    # `compilemessages`. Prod Alpine ships these via apk `gettext`;
    # match that here so dev has parity with CI on translation work.
    gettext \
    # WeasyPrint runtime shared libraries — cairo/pango/gdk-pixbuf are
    # dlopen'd at PDF-generation time, so the image needs them even
    # though the Python wheels live in the .venv. libglib2.0-0 is
    # what provides libgobject-2.0.so.0.
    libglib2.0-0 \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libharfbuzz0b \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    libffi8 \
    # Pango needs actual font files at runtime (without them you get
    # ``pango_font_describe: font != NULL`` criticals and blank PDFs);
    # dejavu covers Latin + Greek, noto-cjk covers CJK in case a
    # product name ever sneaks a Chinese character into an invoice.
    fonts-dejavu-core \
    fonts-noto-core \
    fonts-noto-cjk \
    fontconfig \
    && addgroup --system --gid ${GID} appgroup \
    && adduser --system \
         --uid    ${UID} \
         --gid    ${GID} \
         --home   ${APP_PATH} \
         --shell  /bin/bash \
         appuser \
    && mkdir -p ${APP_PATH}/.cache/uv ${APP_PATH}/staticfiles ${APP_PATH}/mediafiles ${APP_PATH}/logs ${APP_PATH}/backups \
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
