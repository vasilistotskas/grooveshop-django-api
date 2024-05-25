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
