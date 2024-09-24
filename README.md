[![Coverage Status](https://coveralls.io/repos/github/vasilistotskas/grooveshop-django-api/badge.svg?branch=main)](https://coveralls.io/github/vasilistotskas/grooveshop-django-api?branch=main)

# Grooveshop Django API

## Overview

This project delivers a robust headless API using Django and Django REST Framework,
with support for both synchronous and asynchronous environments facilitated by Uvicorn (ASGI)
and Gunicorn (WSGI) respectively. It leverages Django Allauth for authentication,
utilizes Celery with Redis for task management, and employs Postgres for data storage.
Features include caching, multi-language support, and comprehensive test coverage.
The API also includes a built-in Django admin panel for efficient administrative operations.

## Project Structure

The Django applications within this project include:

- **Core**: Core functionalities and shared utilities.
- **User**: User management and authentication.
- **Product**: Product catalog management.
- **Order**: Order processing and management.
- **Search**: Advanced search capabilities.
- **Slider**: Dynamic UI sliders for promotions and highlights.
- **Blog**: Content management for blog posts.
- **SEO**: SEO tools and configurations.
- **Tip**: User tips and advice sections.
- **VAT**: VAT calculation and management.
- **Country**: Country-specific configurations.
- **Region**: Regional data and settings.
- **Pay Way**: Payment method configurations.
- **Session**: User session management.
- **Cart**: Shopping cart functionalities.
- **Notification**: User notification mechanisms.
- **Authentication**: Additional authentication layers.
- **Contact**: Contact management and communication tools.

## Features

- **Authentication and User Management**: Streamlined user account and session management.
- **Multi-Language Support**: Accommodates various languages enhancing global usability.
- **Advanced Search and Filtering**: Leverages Postgres Full-Text Search for efficient data retrieval.
- **Task Scheduling**: Utilizes Celery for background task management.
- **Performance Optimization**: Implements caching strategies to improve API responsiveness.
- **Testing**: Includes comprehensive unit and integration tests.
- **Admin Panel**: Django's built-in admin panel for straightforward management.
- **API Documentation**: Well-documented API using Swagger and Redoc.
- **Containerization**: Docker integration for simplified setup and deployment.

## Technologies

- **Frameworks**: Django, Django REST Framework
- **Authentication**: Django Allauth
- **Database**: PostgreSQL
- **Task Management**: Celery
- **Message Broker**: Redis
- **Server Setup**: Uvicorn (ASGI), Gunicorn (WSGI)
- **Containerization**: Docker

## Setup

### Prerequisites

- Python 3.12 or higher
- Django 5.0 or higher
- PostgreSQL
- Redis

## License

This project is open-sourced under the MIT License. See the [LICENSE](LICENSE.md) file for more details.

# Docker Commands for Django Projects

## Using Docker Compose

### Database Operations
- **Run DB Migrations**:
  `docker compose run backend sh -c "python manage.py makemigrations --noinput"`
- **Apply Migrations**:
  `docker compose run backend sh -c "python manage.py migrate"`

### User Management
- **Create Superuser**:
  `docker compose run backend sh -c "python manage.py createsuperuser"`

### Static Files
- **Collect Static Files**:
  `docker compose run backend sh -c "python manage.py collectstatic --noinput"`

### Testing and Coverage
- **Run Tests**:
  `docker compose run backend sh -c "python manage.py test tests/"`
- **Run Tests with Coverage** (excluding specific files and folders):
  `docker compose run backend sh -c "coverage run --omit=*/migrations/*,*/management/*,*/manage.py,*/setup.py,*/asgi.py,*/wsgi.py --source='.' manage.py test tests/ && coverage report && coverage html"`
- **Generate Coverage HTML Reports**:
  `docker compose run backend sh -c "coverage html"`

### Data Seeding
- **Seed Database with Fake Data**:
  `docker compose run backend sh -c "python manage.py seed_all"`

### Custom Docker Compose Files
- **Run with Specific Compose File**:
  `docker compose -f <docker-compose-file.yml> up -d --build`

## Using Docker Exec

### General Commands
- **Execute Command in Container**:
  `docker exec -it <container_id> <command>`
- **Run Specific Shell Command**:
  `docker exec -it <container_id> sh -c "<command>"`

### Localization
- **Generate Locale Messages**:
  `docker exec -it <container_id> sh -c "python manage.py makemessages -l <locale>"`
  `docker exec -it <container_id> sh -c "python manage.py makemessages --all --ignore=env"`
- **Compile Locale Messages**:
  `docker exec -it <container_id> sh -c "python manage.py compilemessages --ignore=env"`

# Additional Configuration for Development Tools

## Celery

### Starting Celery Services
- **Run a local Celery beat scheduler** using the Django database scheduler:
  `celery -A core beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler`
- **Run a Celery worker**:
  `celery -A core worker -E -l info --pool=solo`
- **Monitor Celery with Flower**:
  `celery -A core flower --broker=amqp://guest:guest@localhost:5672// --broker_api=http://guest:guest@localhost:15672/api// --port=5555`

## Uvicorn

### Running the ASGI Application
- **Start Uvicorn for ASGI applications**:
  `uvicorn asgi:application --port 8000 --workers 4 --log-level debug --reload`

# Python Development Setup and Utilities

## Python Version 3.12.0

### Virtual Environment Management
- **Install Virtualenv**:
  `pip install virtualenv`
- **Create Virtual Environment**:
  `virtualenv <env_name>`
- **Activate Virtual Environment**:
  - Unix/Linux: `source <env_name>/bin/activate`
  - Windows: `<env_name>\Scripts\activate`
- **Deactivate Virtual Environment**:
  `deactivate`
- **Install Requirements**:
  `pip install -r requirements.txt`
- **Install Environment-Specific Requirements**:
  `pip install -r requirements/<env_name>.txt`

### Django Commands
- **Install Django**:
  `pip install django`
- **Start a New Project**:
  `django-admin startproject <project_name>`
- **Start a New App**:
  `python manage.py startapp <app_name>`
- **Database Migrations and Management**:
  - Make migrations: `python manage.py makemigrations`
  - Apply migrations: `python manage.py migrate`
  - Flush the database: `python manage.py sqlflush`
  - Populate database with seed data (Single Factory Example): `python manage.py factory_seed
    --model="BlogPost" --count="100"`
  - Populate database with seed data (All Factories Example): `python manage.py seed_all
    --model-counts="Country=10,Product=100"`
- **Manage Users**:
  - Create superuser: `python manage.py createsuperuser`
- **Manage Static Files**:
  `python manage.py collectstatic`
- **Testing and Debugging**:
  - Run tests: `python manage.py test`
  - Access Django shell: `python manage.py shell`
  - Enhanced shell: `python manage.py shell_plus`
  - Database shell: `python manage.py dbshell`
- **Run Development Server**:
  `python manage.py runserver`

### Code Formatting and Linting
- **Navigate to Source Directory**:
  `cd src`
- **Pre-commit and Black Formatting**:
  - Install pre-commit hooks: `pre-commit install`
  - Run pre-commit hooks: `pre-commit run --all-files`
  - Format code with Black: `black .`

### Poetry for Dependency Management
- **Install Poetry**:
  Unix/Linux: `curl -sSL https://install.python-poetry.org | python3 -`
  Windows: `(Invoke-WebRequest -Uri https://install.python-poetry.org -UseBasicParsing).Content | python -`
- **Manage Projects and Dependencies**:
  - Create a new project: `poetry new <project_name>`
  - Install dependencies: `poetry install`
  - Add or remove a dependency: `poetry add <dependency_name>` and `poetry remove <dependency_name>`
  - Update a specific dependency or lock file: `poetry update <dependency_name>` and `poetry lock`
  - Run a script: `poetry run <script_name>`
  - Enter virtual environment shell: `poetry shell`

### Strawberry GraphQL
- **Install Strawberry**:
  `pip install strawberry-graphql`
- **Run Strawberry Server**:
  `strawberry server`
- **Run with Project Schema**:
  `strawberry server core.graphql.schema:schema`

### Anaconda for Environment Management
- **Install Anaconda**: [Anaconda Installation Guide](https://docs.anaconda.com/anaconda/install/)
- **Conda Environment Commands**:
  - Create: `conda create --name <env_name> python=3.12.0`
  - Activate/Deactivate: `conda activate <env_name>` and `conda deactivate`
  - Create from YML: `conda env create -f environment.yml`

### Django REST Framework - Spectacular
- **Generate API Schema**:
  `python manage.py spectacular --color --file schema.yml`

# Git Command Usage

## Tag Management

### Deleting Tags

- **Delete Remote Tags**:
  - Delete all remote tags:
    `git tag -l | xargs -n 1 git push --delete origin`

- **Delete Local Tags**:
  - Delete all local tags:
    `git tag -l | xargs git tag -d`
