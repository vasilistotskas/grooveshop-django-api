# Email Template Management System

A fully dynamic, configuration-driven email template management system for GrooveShop.

## Quick Start

### View Templates in Admin

Navigate to: `http://localhost:8000/admin/email-templates/management/`

### Test Configuration

```bash
cd grooveshop-django-api
uv run python test_config.py
```

### Generate Previews

```bash
uv run python test_email_templates.py
```

## Architecture

```
core/email/
├── config.py              # ⭐ Central configuration (START HERE)
├── preview_service.py     # Template rendering service
├── registry.py            # Template discovery
├── sample_data.py         # Sample data generators
├── admin_views.py         # Admin interface views
├── urls.py               # URL routing
├── CONFIGURATION_GUIDE.md # Complete guide for adding templates
├── REFACTORING_SUMMARY.md # Technical details of refactoring
└── README.md             # This file
```

## Key Features

### ✅ Configuration-Driven
- All template metadata in one place (`config.py`)
- No hardcoded logic
- Easy to extend

### ✅ Multi-Category Support & Auto-Discovery
- Order templates (`order/`)
- Subscription templates (`subscription/`)
- User management templates (`user/`)
- Marketing templates (`marketing/`)
- Auto-discovers templates based on directory structure
- Easy to add new categories

### ✅ Dynamic Context Generation
- Automatic context selection based on template
- Sample data for previews
- Real data support (e.g., actual orders)

### ✅ Flexible Subject Lines
- Template-based subjects with variable substitution
- Example: `"Order #{order[id]} - {order[status]}"`
- No hardcoded subject maps

### ✅ Admin Interface
- Visual template preview
- Search functionality (Ctrl+K)
- Collapsible categories
- Smart data source selection (Real Order only for order templates)
- Real-time rendering
- Sample and real data support

## Adding a New Template

### 1. Create Template Files

```html
<!-- core/templates/emails/user/welcome.html -->
<!DOCTYPE html>
<html>
<body>
    <h1>Welcome, {{ user.first_name }}!</h1>
    <p>Thanks for joining!</p>
</body>
</html>
```

### 2. Add Configuration

Edit `config.py`:

```python
# In EmailTemplateConfig.TEMPLATES dict:

"welcome": TemplateConfig(
    name="welcome",
    category_name="User Management",
    description="Welcome email for new users",
    subject_template="Welcome, {user[first_name]}!",
    is_used=True,
    context_keys=["user"],
    # order_statuses is optional - only needed for order templates
),
```

### 3. Done!

That's it! The template is now:
- Discoverable in admin
- Previewable with sample data
- Has correct subject line
- Uses appropriate context

## Configuration Structure

### Template Configuration

```python
TemplateConfig(
    name="template_name",           # File name without extension
    category_name="Category Name",  # Display category
    description="What this does",   # Description
    subject_template="Subject {var}", # Subject with variables
    order_statuses=[],              # Related order statuses
    is_used=True,                   # Is actively used?
    context_keys=["key1", "key2"],  # Required context variables
)
```

### Category Configuration

```python
TemplateCategory(
    name="Category Name",           # Display name
    path="subdirectory",            # Path (empty for root)
    context_generator="method_name", # Context generator method
    templates={},                   # Auto-populated
)
```

## Context Generators

Available context generators:

- `generate_order_context` - Order data (order, items, tracking)
- `generate_subscription_context` - Subscription data
- `generate_user_context` - User data
- `generate_marketing_context` - Marketing data (newsletters)

### Adding a Custom Generator

1. Add method to `preview_service.py`:

```python
def _get_sample_custom_context(self) -> dict:
    return {
        "custom_data": {...},
        "user": {...},
    }
```

2. Register in `_get_context_data_for_category`:

```python
generator_map = {
    # ... existing generators
    "generate_custom_context": lambda: (
        self._get_sample_custom_context(),
        True
    ),
}
```

3. Use in category configuration:

```python
"custom": TemplateCategory(
    name="Custom",
    path="custom",
    context_generator="generate_custom_context",
    templates={},
),
```

## Subject Line Variables

Subject templates support variable substitution:

### Simple Variables
```python
"Welcome, {user[first_name]}!"
# → "Welcome, John!"
```

### Nested Access
```python
"Order #{order[id]} - {order[status]}"
# → "Order #12345 - Shipped"
```

### Multiple Variables
```python
"{user[first_name]}, your {subscription[plan]} is active!"
# → "John, your Premium is active!"
```

## Testing

### Configuration Test
```bash
uv run python test_config.py
```

Tests:
- Configuration loading
- Category detection
- Context generator detection
- Preview generation
- Registry integration

### Template Preview Test
```bash
uv run python test_email_templates.py
```

Generates HTML previews for all templates in `email_previews/` directory.

## Admin Interface

### Features

- **Template List** - All templates grouped by category
- **Live Preview** - Real-time HTML/text preview
- **Sample Data** - Preview with generated sample data
- **Real Data** - Preview with actual order data
- **Subject Preview** - See formatted subject lines
- **Category Filter** - Filter by template category

### URL Endpoints

- `/admin/email-templates/management/` - Main interface
- `/admin/email-templates/preview/` - AJAX preview endpoint
- `/admin/email-templates/info/<name>/` - Template info
- `/admin/email-templates/order/<id>/` - Order data

## Directory Structure

```
core/templates/emails/
├── base/
│   └── email_base.html          # Base template
├── order/
│   ├── order_confirmation.html
│   ├── order_confirmation.txt
│   ├── order_shipped.html
│   ├── order_shipped.txt
│   └── ...
├── subscription/
│   ├── confirmation.html
│   └── ...
├── inactive_user_email_template.html  # Root level
├── newsletter.html                     # Root level
└── ...
```

## Best Practices

### 1. Naming Conventions
- Template files: `snake_case.html`
- Categories: `PascalCase` for display
- Context generators: `generate_*_context`

### 2. Context Keys
Always document required context keys:
```python
context_keys=["user", "order", "items"]
```

### 3. Subject Templates
Keep subjects concise and use clear variable names:
```python
subject_template="Order #{order[id]} Update"  # ✅ Good
subject_template="Your order {order[id]} has been {order[status]}"  # ❌ Too long
```

### 4. Categories
Group related templates logically:
- Order lifecycle → "Order Lifecycle"
- Shipping updates → "Shipping"
- User actions → "User Management"
- Promotions → "Marketing"

### 5. Testing
Always test new templates:
```bash
uv run python test_config.py
```

## Troubleshooting

### Template not appearing
- Check `config.py` has entry
- Verify file exists in correct directory
- Check category path matches directory

### Wrong context
- Verify `context_generator` in category config
- Check generator method exists
- Ensure generator is in `generator_map`

### Subject not formatting
- Check syntax: `{key[subkey]}`
- Verify context has required keys
- Check for typos

### Preview errors
- Check template syntax
- Verify all context variables exist
- Check template path in config

## Documentation

- **CONFIGURATION_GUIDE.md** - Complete guide for adding templates
- **REFACTORING_SUMMARY.md** - Technical details of refactoring
- **ADMIN_GUIDE.md** - Admin interface usage guide

## Support

For issues or questions:
1. Check documentation files
2. Run test scripts
3. Review configuration in `config.py`
4. Check Django logs for errors

## Version History

### v2.0 (Current) - Configuration-Driven
- ✅ Centralized configuration
- ✅ Dynamic category system
- ✅ Template-driven subjects
- ✅ No hardcoded logic
- ✅ Easy to extend

### v1.0 (Legacy) - Hardcoded
- ❌ Scattered configuration
- ❌ Hardcoded categories
- ❌ Hardcoded subjects
- ❌ Difficult to maintain
