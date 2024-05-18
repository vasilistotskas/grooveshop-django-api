###########
# BUILDER #
###########

# pull official base image
FROM python:3.12.2-slim as builder

# set work directory
WORKDIR /usr/src/app

# set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# install system dependencies
RUN apt-get update && \
    apt-get install -y gcc \
    postgresql-client \
    libpq-dev \
    python3-dev \
    libc-dev \
    linux-headers-amd64 \
    build-essential \
    git

# install python dependencies
COPY ./requirements.txt .
RUN pip wheel --no-cache-dir --no-deps --wheel-dir /usr/src/app/wheels -r requirements.txt

#########
# FINAL #
#########

# pull official base image
FROM python:3.12.2-slim

# create directory for the app user
RUN mkdir -p /home/app

# create the app user
RUN groupadd app && useradd -g app app

# create the appropriate directories
ENV HOME=/home/app
ENV APP_HOME=/home/app/web
RUN mkdir $APP_HOME
RUN mkdir $APP_HOME/staticfiles
RUN mkdir $APP_HOME/mediafiles
WORKDIR $APP_HOME

# install dependencies
RUN apt-get update && apt-get install -y netcat-openbsd
COPY --from=builder /usr/src/app/wheels /wheels
COPY --from=builder /usr/src/app/requirements.txt .
RUN pip install --upgrade pip
RUN pip install --no-cache /wheels/*

# install psycopg binary and pool
RUN pip install "psycopg[binary,pool]"

# copy entrypoint.prod.sh
COPY ./docker/entrypoint.prod.sh .
RUN sed -i 's/\r$//g'  $APP_HOME/entrypoint.prod.sh
RUN chmod +x  $APP_HOME/entrypoint.prod.sh

# copy project
COPY . $APP_HOME

# create empty logs folder
RUN mkdir $APP_HOME/logs

# chown all the files to the app user
RUN chown -R app:app $APP_HOME

# change to the app user
USER app

# run entrypoint.prod.sh
ENTRYPOINT ["/home/app/web/entrypoint.prod.sh"]

# run uvicorn
CMD ["uvicorn", "asgi:application", "--host", "0.0.0.0", "--port", "8000"]
