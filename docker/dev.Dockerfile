# pull official base image
FROM python:3.12-alpine

VOLUME /mnt/app

# set work directory
WORKDIR /mnt/app

# set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# install system dependencies
RUN apk --no-cache add netcat-openbsd gcc musl-dev

# install dependencies
RUN pip install --upgrade pip
COPY ./grooveshop-django-api/requirements.txt /mnt/app/requirements.txt
RUN pip install -r requirements.txt

# copy entrypoint.sh
COPY ./grooveshop-django-api/docker/entrypoint.sh /docker-entrypoint.sh
RUN sed -i 's/\r$//g' /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# run entrypoint.sh
ENTRYPOINT ["/docker-entrypoint.sh"]
