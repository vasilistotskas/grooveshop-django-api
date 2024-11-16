###########
# BUILDER #
###########

# Pull official base image
FROM python:3.13.0-slim-bookworm AS builder

# Set work directory
WORKDIR /usr/src/app

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    build-essential \
    git && \
    rm -rf /var/lib/apt/lists/*

# Install pip
RUN pip install --upgrade pip

# Install python dependencies
COPY ./requirements.txt .
RUN pip wheel --no-cache-dir --no-deps --wheel-dir /usr/src/app/wheels -r requirements.txt

#########
# FINAL #
#########

# Pull official base image
FROM python:3.13.0-slim-bookworm

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/app \
    APP_HOME=/home/app/web

# Create the app user and directories
RUN groupadd -r app && useradd -r -g app app && \
    mkdir -p $APP_HOME/staticfiles $APP_HOME/mediafiles $APP_HOME/logs && \
    chown -R app:app $APP_HOME

# Set work directory
WORKDIR $APP_HOME

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends netcat-openbsd && \
    rm -rf /var/lib/apt/lists/*

# Copy python dependencies from builder
COPY --from=builder /usr/src/app/wheels /wheels
COPY --from=builder /usr/src/app/requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir /wheels/*

# Install psycopg binary and pool
RUN pip install --no-cache-dir "psycopg[binary,pool]"

# Copy entrypoint script and set ownership
COPY --chown=app:app ./docker/entrypoint.prod.sh .

# Ensure entrypoint script has execution permissions
RUN chmod +x entrypoint.prod.sh

# Copy project files and set ownership
COPY --chown=app:app . .

# Switch to app user
USER app

# Expose port (if applicable)
EXPOSE 8000

# Run entrypoint script
ENTRYPOINT ["./entrypoint.prod.sh"]

# Run application
CMD ["uvicorn", "asgi:application", "--host", "0.0.0.0", "--port", "8000"]
