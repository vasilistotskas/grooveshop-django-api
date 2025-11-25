# Email Template Management - Configuration Guide

## Overview

The email template management system is now fully **configuration-driven** and **dynamic**. All template metadata, categories, context generators, and subject lines are defined in a central configuration file, eliminating hardcoded logic throughout the codebase.

## Architecture

### Core Components

1. **`config.py`** - Central configuration for all templates
2. **`preview_service.py`** - Template rendering service (uses config)
3. **`registry.py`** - Template discovery service (uses config)
4. **`sample_data.py`** - Sample data generators

### Configuration File Structure

```python
# core/email/config.py

@dataclass
class TemplateCategory:
    name: str                    # Display name
    path: str                    # Subdirectory path (empty for root)
    context_generator: str       # Method name to generate context
    templates: dict              # Template configurations

@dataclass
class TemplateConfig:
    name: str                    # Template file name
    category_name: str           # Display category
    description: str             # Template description
    subject_template: str        # Subject with {variables}
    is_used: bool               # Whether actively used
    context_keys: list          # Required context keys
    order_statuses: list = None # Associated order statuses (optional, only for order templates)
```

## Adding a New Template

### Step 1: Create Template Files

Create your HTML and TXT templates in the appropriate directory:

```
core/templates/emails/
├── order/              # Order-related templates
├── subscription/       # Subscription templates
├── user/              # User management templates
└── marketing/         # Marketing templates
```

**Auto-Discovery**: Templates are automatically discovered based on their directory location.

Example: `core/templates/emails/user/password_reset.html`

### Step 2: Add Configuration

Add your template to `config.py`:

```python
# In EmailTemplateConfig.TEMPLATES dict:

"password_reset": TemplateConfig(
    name="password_reset",
    category_name="User Management",
    description="Password reset request email",
    subject_template="Reset Your Password - {user[first_name]}",
    is_used=True,
    context_keys=["user", "reset_link", "expiry_time"],
    # order_statuses is optional - omit for non-order templates
),
```

### Step 3: Add Context Generator (if needed)

If your template needs a new type of context, add a generator method to `preview_service.py`:

```python
def _get_sample_password_reset_context(self) -> dict:
    """Generate sample password reset data."""
    from datetime import datetime, timedelta

    return {
        "user": {
            "id": 12345,
            "username": "john_doe",
            "first_name": "John",
            "email": "john.doe@example.com",
        },
        "reset_link": f"{settings.NUXT_BASE_URL}/reset-password/abc123",
        "expiry_time": datetime.now() + timedelta(hours=24),
    }
```

Then register it in the category configuration:

```python
# In EmailTemplateConfig.CATEGORIES dict:

"user": TemplateCategory(
    name="User Management",
    path="user",  # Matches directory name for auto-discovery
    path="user",  # or "" for root level
    context_generator="generate_user_context",  # or your new method
    templates={},
),
```

### Step 4: Test

Run the test script to verify:

```bash
uv run python test_config.py
```

## Adding a New Category

### Step 1: Define Category

Add to `EmailTemplateConfig.CATEGORIES`:

```python
"payment": TemplateCategory(
    name="Payment",
    path="payment",
    context_generator="generate_payment_context",
    templates={},
),
```

### Step 2: Create Context Generator

Add method to `preview_service.py`:

```python
def _get_sample_payment_context(self) -> dict:
    """Generate sample payment data."""
    return {
        "payment": {
            "id": 12345,
            "amount": "€99.99",
            "status": "completed",
        },
        "user": {
            "first_name": "John",
            "email": "john@example.com",
        },
    }
```

### Step 3: Register in Context Generator Map

Update `_get_context_data_for_category` method:

```python
generator_map = {
    "generate_order_context": lambda: self._get_context_data(order_id),
    "generate_subscription_context": lambda: (self._get_sample_subscription_context(), True),
    "generate_user_context": lambda: (self._get_sample_user_context(), True),
    "generate_marketing_context": lambda: (self._get_sample_user_context(), True),
    "generate_payment_context": lambda: (self._get_sample_payment_context(), True),  # NEW
}
```

## Subject Line Templates

Subject lines support variable substitution:

### Simple Variables
```python
subject_template="Welcome, {user[first_name]}!"
# Result: "Welcome, John!"
```

### Nested Dictionary Access
```python
subject_template="Order #{order[id]} - {order[status]}"
# Result: "Order #12345 - Shipped"
```

### Multiple Variables
```python
subject_template="{user[first_name]}, your order #{order[id]} is ready!"
# Result: "John, your order #12345 is ready!"
```

## Context Keys Validation

The `context_keys` list defines required context variables for each template. This enables:

1. **Documentation** - Clear requirements for each template
2. **Validation** - Future validation of context completeness
3. **Testing** - Automated testing of context generation

Example:
```python
context_keys=["user", "order", "items", "tracking_number"]
```

## Benefits of Configuration-Driven Approach

### 1. **No Hardcoded Logic**
- All template metadata in one place
- Easy to understand and maintain
- No scattered if/else chains

### 2. **Easy to Extend**
- Add new templates: Just add config entry
- Add new categories: Define category + context generator
- No code changes in multiple files

### 3. **Self-Documenting**
- Configuration serves as documentation
- Clear relationships between templates and categories
- Subject templates show expected variables

### 4. **Type-Safe**
- Dataclasses provide type checking
- IDE autocomplete support
- Catch errors at development time

### 5. **Testable**
- Configuration can be validated
- Context generators are isolated
- Easy to mock and test

## Migration from Hardcoded System

The old system had:
- Hardcoded category detection in `_extract_category()`
- Hardcoded subject maps in `_generate_subject()`
- Hardcoded metadata in `registry.py`
- Scattered template information

The new system:
- ✅ Single source of truth in `config.py`
- ✅ Dynamic category detection
- ✅ Template-driven subject generation
- ✅ Centralized metadata
- ✅ Easy to extend without code changes

## Example: Complete Template Addition

Let's add a "Welcome Email" template:

### 1. Create Template
```html
<!-- core/templates/emails/user/welcome.html -->
<!DOCTYPE html>
<html>
<body>
    <h1>Welcome, {{ user.first_name }}!</h1>
    <p>Thanks for joining {{ SITE_NAME }}!</p>
    <a href="{{ activation_link }}">Activate Your Account</a>
</body>
</html>
```

### 2. Add Configuration
```python
# In config.py - EmailTemplateConfig.TEMPLATES

"welcome": TemplateConfig(
    name="welcome",
    category_name="User Management",
    description="Welcome email for new users",
    subject_template="Welcome to {SITE_NAME}, {user[first_name]}!",
    order_statuses=[],
    is_used=True,
    context_keys=["user", "activation_link", "SITE_NAME"],
),
```

### 3. Ensure Context Generator Exists
```python
# Already exists in preview_service.py
def _get_sample_user_context(self) -> dict:
    return {
        "user": {
            "first_name": "John",
            "email": "john@example.com",
        },
        "activation_link": f"{settings.NUXT_BASE_URL}/activate/abc123",
        # SITE_NAME added automatically by _render_template
    }
```

### 4. Done!
The template is now:
- ✅ Discoverable by registry
- ✅ Previewable in admin
- ✅ Has correct subject line
- ✅ Uses appropriate context

## Best Practices

1. **Keep categories logical** - Group related templates together
2. **Use descriptive names** - Template names should be self-explanatory
3. **Document context keys** - List all required variables
4. **Test thoroughly** - Use `test_config.py` to verify
5. **Follow naming conventions**:
   - Template files: `snake_case.html`
   - Categories: `PascalCase` for display
   - Context generators: `generate_*_context`

## Troubleshooting

### Template not appearing in admin
- Check configuration in `config.py`
- Verify template file exists in correct directory
- Check category path matches directory structure

### Wrong context data
- Verify `context_generator` in category config
- Check generator method exists in `preview_service.py`
- Ensure generator is registered in `generator_map`

### Subject line not formatting
- Check subject_template syntax: `{key[subkey]}`
- Verify context contains required keys
- Check for typos in variable names

## Future Enhancements

Potential improvements to the configuration system:

1. **YAML/JSON Configuration** - Move config to external file
2. **Context Validation** - Validate context against `context_keys`
3. **Template Versioning** - Track template versions
4. **A/B Testing** - Support multiple subject lines
5. **Localization** - Multi-language subject templates
6. **Dynamic Discovery** - Auto-detect templates without config
