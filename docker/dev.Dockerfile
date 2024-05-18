# pull official base image
FROM python:3.12-alpine

VOLUME /mnt/app

# set work directory
WORKDIR /mnt/app

# set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Update apk and install system dependencies
RUN apk update && \
    apk add --no-cache \
    netcat-openbsd \
    gcc \
    musl-dev \
    postgresql-client \
    postgresql-dev \
    git

# install pip
RUN pip install --upgrade pip

# copy requirements file
COPY ./grooveshop-django-api/requirements.txt /mnt/app/requirements.txt

# install dependencies
RUN pip install -r requirements.txt

# copy entrypoint.sh
COPY ./grooveshop-django-api/docker/entrypoint.sh /docker-entrypoint.sh
RUN sed -i 's/\r$//g' /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# run entrypoint.sh
ENTRYPOINT ["/docker-entrypoint.sh"]
