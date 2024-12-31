# pull official base image
FROM python:3.13.1-slim-bookworm

# set work directory
WORKDIR /mnt/app

# set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Update apt and install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    netcat-openbsd \
    gcc \
    build-essential \
    libpq-dev \
    git && \
    rm -rf /var/lib/apt/lists/*

# install pip
RUN pip install --upgrade pip

# copy requirements file
COPY ./grooveshop-django-api/requirements.txt /mnt/app/requirements.txt

# install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# copy entrypoint.sh
COPY ./grooveshop-django-api/docker/entrypoint.sh /docker-entrypoint.sh
RUN sed -i 's/\r$//g' /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# run entrypoint.sh
ENTRYPOINT ["/docker-entrypoint.sh"]
