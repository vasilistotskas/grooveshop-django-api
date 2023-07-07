# Base build
FROM python:3.11-alpine as base

RUN python -m venv /py && \
    /py/bin/pip install --upgrade pip setuptools wheel && \
    apk add --update --no-cache postgresql-client && \
    apk add --update --no-cache libffi-dev && \
    apk add --update --no-cache jpeg-dev zlib-dev && \
    apk add --update --no-cache libc-dev && \
    apk add --update --no-cache gcc && \
    apk add --update --no-cache freetype-dev && \
    apk add --update --no-cache libjpeg-turbo-dev && \
    apk add --update --no-cache libpng-dev && \
    apk add --update --no-cache --virtual .tmp-deps \
        build-base postgresql-dev musl-dev linux-headers && \
    apk del .tmp-deps

COPY ./requirements.txt /requirements.txt
RUN pip install -r /requirements.txt

FROM python:3.11-alpine
LABEL maintainer="groove.com"

COPY --from=base /usr/local/lib/python3.11/site-packages/ /usr/local/lib/python3.11/site-packages/
COPY --from=base /usr/local/bin/ /usr/local/bin/

COPY ./ /src
COPY ./scripts /scripts

WORKDIR /src

RUN adduser --disabled-password --no-create-home backend && \
    mkdir -p /src/backend/static && \
    mkdir -p /src/backend/media && \
    mkdir -p /src/backend/files && \
    mkdir -p /src/backend/logs && \
    chown -R backend:backend /src && \
    chmod -R 755 /src && \
    chmod -R +x /scripts

ENV PYTHONUNBUFFERED 1
ENV PYTHONDONTWRITEBYTECODE 1
ENV PATH="/scripts:/py/bin:$PATH"
ENV LIBRARY_PATH=/lib:/usr/lib

USER backend
