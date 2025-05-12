# GrooveShop Django API Development Guidelines

This document provides essential information for developers working on the GrooveShop Django API project.

## Build/Configuration Instructions

### Environment Setup

1. **Python Version**: The project requires Python 3.11+ (currently using 3.13.2)

2. **Dependencies Management**:
   - The project uses Poetry for dependency management
   - Alternatively, pip with requirements.txt can be used

   ```bash
   # Using Poetry
   poetry install

   # Using pip
   pip install -r requirements.txt
   ```

3. **Environment Variables**:
   - Copy `.env.example` to `.env` and configure the variables
   - Key environment variables include:
     - Django configuration (DEBUG, SECRET_KEY, etc.)
     - Database configuration
     - Redis and Celery settings
     - MeiliSearch configuration
     - AWS settings (if using S3 for storage)

### Docker Setup

The project is containerized using Docker with multiple services:

1. **Application Services** (app.compose.yml):
   - backend-init: Runs migrations and collects static files
   - backend: Main Django application
   - celery_worker: For asynchronous task processing
   - celery_beat: For scheduled tasks
   - celery_flower: For monitoring Celery tasks

2. **Infrastructure Services** (infra.compose.yml):
   - PostgreSQL database
   - Redis for caching and Channels
   - RabbitMQ for message queuing
   - MeiliSearch for search functionality
   - PgAdmin for database management
   - RedisInsight for Redis monitoring

3. **Running with Docker**:
   ```bash
   # Start infrastructure services
   docker-compose -f infra.compose.yml up -d

   # Start application services
   docker-compose -f app.compose.yml up -d
   ```

### Local Development Setup

1. **Database Setup**:
   - Configure PostgreSQL connection in `.env`
   - Run migrations: `python manage.py migrate`

2. **Static Files**:
   - Collect static files: `python manage.py collectstatic`

3. **Running the Server**:
   - Development server: `python manage.py runserver`
   - With Uvicorn: `uvicorn asgi:application --host 0.0.0.0 --port 8000 --reload`

## Testing Information

### Test Configuration

The project uses pytest for testing with Django integration:

- Configuration is in `pytest.ini` and `pyproject.toml`
- Tests are organized into unit and integration tests
- Factory Boy is used for generating test data

### Running Tests

```bash
# Run all tests
python -m pytest

# Run specific test file
python -m pytest tests/path/to/test_file.py

# Run with verbosity
python -m pytest -v

# Run with coverage
coverage run -m pytest
coverage report
```

### Adding New Tests

1. **Test Structure**:
   - Unit tests go in `tests/unit/`
   - Integration tests go in `tests/integration/`
   - Follow the existing module structure

2. **Test Classes**:
   - Use Django's `TestCase` for tests requiring database
   - Use pytest fixtures for reusable test components
   - Use factories to create test data

3. **Example Test**:

```python
from django.test import TestCase

class ExampleTestCase(TestCase):
    def setUp(self):
        # Setup test data
        pass

    def test_example_functionality(self):
        # Test logic
        self.assertEqual(expected, actual)
```

### Test Factories

The project uses Factory Boy for generating test data:

- Factory classes are defined in `app_name/factories.py`
- Use factories to create model instances for tests
- Example usage:
  ```python
  user = UserAccountFactory(num_addresses=0)
  product = ProductFactory(num_images=0, num_reviews=0)
  ```

## Additional Development Information

### Code Style

1. **Linting and Formatting**:
   - The project uses Ruff for linting and formatting
   - Configuration is in `pyproject.toml`
   - Pre-commit hooks are configured in `.pre-commit-config.yaml`

2. **Editor Configuration**:
   - EditorConfig is used for consistent coding style
   - Python files use 4-space indentation
   - Maximum line length is 108 characters

3. **Pre-commit Hooks**:
   - Install pre-commit: `pip install pre-commit`
   - Install hooks: `pre-commit install`
   - Hooks include:
     - YAML validation
     - End-of-file fixing
     - Trailing whitespace removal
     - Code coverage check
     - Ruff linting and formatting

### Project Structure

The project follows a modular structure with Django apps:

- Each app has its own models, views, services, and serializers
- API endpoints are defined in app-specific views
- Business logic is encapsulated in service classes
- Factory classes are used for test data generation

### Asynchronous Tasks

The project uses Celery for asynchronous task processing:

- Celery workers process background tasks
- Celery Beat handles scheduled tasks
- Flower provides a web UI for monitoring tasks
- RabbitMQ is used as the message broker

### Caching

The project uses Redis for caching:

- Custom cache implementation in `core.caches`
- Cache keys should follow a consistent naming pattern
- Cache invalidation should be handled carefully

### Search

The project uses MeiliSearch for search functionality:

- MeiliSearch is a powerful, fast, and open-source search engine
- Configuration is in the environment variables
- Search indexes are defined in the search app