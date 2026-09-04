# CONTAINS Operator Documentation

## Overview

The CONTAINS operator is an **experimental Meilisearch feature** that enables substring matching within field values. This allows for more flexible filtering compared to exact match or prefix matching.

⚠️ **Warning**: This is an experimental feature and must be explicitly enabled in Meilisearch before use.

## Enabling the Feature

### Using Management Command

```bash
python manage.py meilisearch_enable_experimental --feature containsFilter
```

### Using Meilisearch API Directly

```bash
curl -X POST 'http://localhost:7700/experimental-features' \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer YOUR_MASTER_KEY' \
  --data-binary '{
    "containsFilter": true
  }'
```

### Verifying Feature Status

```bash
curl -X GET 'http://localhost:7700/experimental-features' \
  -H 'Authorization: Bearer YOUR_MASTER_KEY'
```

Expected response:
```json
{
  "containsFilter": true
}
```

## Usage

### Django QuerySet API

The CONTAINS operator is available through the `__contains` lookup in the IndexQuerySet API:

```python
from product.models import ProductTranslation

# Find products with "laptop" anywhere in the name
results = ProductTranslation.meilisearch.filter(name__contains="laptop")

# Find products with "pro" in the description
results = ProductTranslation.meilisearch.filter(description__contains="pro")

# Combine with other filters
results = ProductTranslation.meilisearch.filter(
    name__contains="laptop", language_code="en", active=True
)
```

### Generated Filter Syntax

The `__contains` lookup generates Meilisearch filter expressions using the CONTAINS operator:

```python
# Django QuerySet
ProductTranslation.meilisearch.filter(name__contains="laptop")

# Generated Meilisearch filter
"name CONTAINS 'laptop'"
```

### Case Sensitivity

By default, CONTAINS performs **case-insensitive** matching:

```python
# All of these will match "Laptop Pro 15"
ProductTranslation.meilisearch.filter(name__contains="laptop")
ProductTranslation.meilisearch.filter(name__contains="LAPTOP")
ProductTranslation.meilisearch.filter(name__contains="Laptop")
```

## Supported Field Types

`_add_contains_filter` (`meili/querysets.py:307-315`) validates the **value**
you pass, not the field it's applied to — any string value is accepted
regardless of which field it's filtered against. Only a non-string value
(e.g. an `int`, `bool`, or `None`) raises `TypeError` client-side.

### ✅ Valid Usage

```python
# String fields, string values
ProductTranslation.meilisearch.filter(name__contains="laptop")
ProductTranslation.meilisearch.filter(description__contains="high-performance")
BlogPostTranslation.meilisearch.filter(title__contains="guide")

# A string value against a numeric/boolean/date field does NOT raise —
# the CONTAINS filter is still generated and sent to Meilisearch.
# Whether it matches anything (or Meilisearch rejects it) is a
# Meilisearch-side concern, not something this code validates.
ProductTranslation.meilisearch.filter(final_price__contains="99")
```

### ❌ Invalid Usage

```python
# Non-string VALUE - raises TypeError, regardless of field
ProductTranslation.meilisearch.filter(final_price__contains=99)
# TypeError: CONTAINS operator only supports string values, not int. ...

ProductTranslation.meilisearch.filter(active__contains=True)
# TypeError: CONTAINS operator only supports string values, not bool. ...

BlogPostTranslation.meilisearch.filter(created_at__contains=None)
# TypeError: CONTAINS operator only supports string values, not NoneType. ...
```

## Examples

### Product Search

```python
from product.models import ProductTranslation

# Find all laptops (matches "Laptop", "Gaming Laptop", "Laptop Pro", etc.)
laptops = ProductTranslation.meilisearch.filter(
    name__contains="laptop", language_code="en"
)

# Find products with "wireless" in description
wireless_products = ProductTranslation.meilisearch.filter(
    description__contains="wireless", active=True
)

# Find products with model numbers containing "X1"
x1_products = ProductTranslation.meilisearch.filter(name__contains="X1")
```

### Blog Post Search

```python
from blog.models import BlogPostTranslation

# Find blog posts with "tutorial" in title
tutorials = BlogPostTranslation.meilisearch.filter(
    title__contains="tutorial", is_published=True
)

# Find posts mentioning "Python" in body
python_posts = BlogPostTranslation.meilisearch.filter(
    body__contains="Python", language_code="en"
)
```

### Complex Queries

```python
# Combine CONTAINS with other lookups
results = ProductTranslation.meilisearch.filter(
    name__contains="pro",
    final_price__gte=500,
    final_price__lte=2000,
    language_code="en",
    active=True,
)

# Multiple CONTAINS filters
results = ProductTranslation.meilisearch.filter(
    name__contains="laptop", description__contains="gaming"
)
```

## Performance Considerations

### Index Size Impact

CONTAINS filtering may be slower than exact match or prefix matching, especially on large indexes. Consider these optimizations:

1. **Use searchCutoffMs**: Set a timeout to prevent long-running queries
   ```python
   class MeiliMeta:
       search_cutoff_ms = 1500  # 1.5 second timeout
   ```

2. **Combine with other filters**: Narrow down results before applying CONTAINS
   ```python
   # Better performance
   results = ProductTranslation.meilisearch.filter(
       category="Computers",  # Narrow down first
       name__contains="laptop",  # Then apply CONTAINS
   )
   ```

3. **Use full-text search when possible**: For general text search, use the search query instead of CONTAINS
   ```python
   # Prefer this for general search
   results = ProductTranslation.meilisearch.search("laptop")

   # Use CONTAINS for specific substring filtering
   results = ProductTranslation.meilisearch.filter(name__contains="X1")
   ```

### Query Optimization

```python
# ❌ Slow: CONTAINS on large text field without other filters
results = ProductTranslation.meilisearch.filter(description__contains="the")

# ✅ Better: Combine with specific filters
results = ProductTranslation.meilisearch.filter(
    category="Electronics", active=True, description__contains="wireless"
)

# ✅ Best: Use full-text search for general queries
results = ProductTranslation.meilisearch.search("wireless electronics")
```

## Error Handling

### Type Validation

```python
from product.models import ProductTranslation

try:
    # This will raise TypeError — the VALUE (99, an int) is not a string.
    # A string value like final_price__contains="99" would NOT raise here.
    results = ProductTranslation.meilisearch.filter(final_price__contains=99)
except TypeError as e:
    print(f"Error: {e}")
    # Error: CONTAINS operator only supports string values, not int. ...
```

### Feature Not Enabled

If the experimental feature is not enabled, Meilisearch will return an error:

```python
try:
    results = ProductTranslation.meilisearch.filter(name__contains="laptop")
except Exception as e:
    print(f"Error: {e}")
    # Error: The `CONTAINS` filter operator is experimental and must be enabled
```

**Solution**: Enable the feature using the management command:
```bash
python manage.py meilisearch_enable_experimental --feature containsFilter
```

## Comparison with Other Lookups

### Exact Match (`__exact` or no suffix)

```python
# Exact match - only matches "Laptop Pro 15" exactly
ProductTranslation.meilisearch.filter(name="Laptop Pro 15")
ProductTranslation.meilisearch.filter(name__exact="Laptop Pro 15")
```

### CONTAINS

```python
# Substring match - matches any product with "Pro" in the name
# "Laptop Pro 15", "MacBook Pro", "Pro Gaming Mouse", etc.
ProductTranslation.meilisearch.filter(name__contains="Pro")
```

### Full-Text Search

```python
# Full-text search - uses Meilisearch ranking and typo tolerance
# Best for general search queries
ProductTranslation.meilisearch.search("laptop pro")
```

## Best Practices

### 1. Use for Specific Substring Matching

CONTAINS is ideal for finding specific substrings like model numbers or codes:

```python
# Find products with model number containing "X1"
ProductTranslation.meilisearch.filter(name__contains="X1")

# Find products with SKU containing "ELEC"
ProductTranslation.meilisearch.filter(sku__contains="ELEC")
```

### 2. Combine with Other Filters

Always combine CONTAINS with other filters to improve performance:

```python
# Good: Narrow down by category first
ProductTranslation.meilisearch.filter(category="Laptops", name__contains="pro")

# Bad: CONTAINS on entire index
ProductTranslation.meilisearch.filter(name__contains="pro")
```

### 3. Prefer Full-Text Search for General Queries

Use full-text search for general text queries:

```python
# ✅ Use search() for general queries
ProductTranslation.meilisearch.search("gaming laptop")

# ❌ Don't use CONTAINS for general search
ProductTranslation.meilisearch.filter(name__contains="gaming").filter(
    name__contains="laptop"
)
```

### 4. Set Search Cutoff

Always configure searchCutoffMs to prevent long-running queries:

```python
class ProductTranslation(models.Model):
    class MeiliMeta:
        search_cutoff_ms = 1500  # 1.5 second timeout
        # ... other settings
```

### 5. Prefer CONTAINS on Genuinely Textual Fields, and Always Pass a String Value

The lookup only validates that the **value** is a `str` — it does not check
that the field itself is textual. Passing a string against a numeric/boolean/
date field won't raise in Python, but it's still the wrong tool for the job:

```python
# ✅ String fields, string values
name__contains = "laptop"
description__contains = "wireless"
sku__contains = "ELEC"

# ⚠️ Doesn't raise, but semantically wrong — use range/exact lookups instead
price__contains = "99"  # Use price__gte, price__lte instead
active__contains = "true"  # Use active=True instead
created_at__contains = "2024"  # Use date range filters instead

# ❌ Non-string VALUE - raises TypeError
price__contains = 99
```

## Testing

### Unit Tests

```python
import pytest
from product.models import ProductTranslation


def test_contains_filter_on_string_field():
    """Test CONTAINS operator on string field."""
    results = ProductTranslation.meilisearch.filter(name__contains="laptop")
    assert all("laptop" in r.name.lower() for r in results)


def test_contains_filter_case_insensitive():
    """Test CONTAINS is case-insensitive."""
    results_lower = ProductTranslation.meilisearch.filter(
        name__contains="laptop"
    )
    results_upper = ProductTranslation.meilisearch.filter(
        name__contains="LAPTOP"
    )
    assert list(results_lower) == list(results_upper)


def test_contains_filter_on_non_string_value_raises_error():
    """Test CONTAINS with a non-string VALUE raises TypeError.

    A string value against a numeric field (e.g. ``final_price__contains="99"``)
    does NOT raise — only a non-string value does, regardless of field.
    """
    with pytest.raises(TypeError, match="only supports string values"):
        ProductTranslation.meilisearch.filter(final_price__contains=99)
```

### Integration Tests

```python
def test_contains_filter_with_real_data():
    """Test CONTAINS filter with real Meilisearch data."""
    # Create test products
    ProductTranslation.objects.create(name="Laptop Pro 15", language_code="en")
    ProductTranslation.objects.create(
        name="Gaming Laptop X1", language_code="en"
    )

    # Sync to Meilisearch
    ProductTranslation.meilisearch.sync()

    # Test CONTAINS filter
    results = ProductTranslation.meilisearch.filter(
        name__contains="laptop", language_code="en"
    )

    assert len(results) == 2
    assert all("laptop" in r.name.lower() for r in results)
```

## Troubleshooting

### Feature Not Enabled

**Error**: `The CONTAINS filter operator is experimental and must be enabled`

**Solution**:
```bash
python manage.py meilisearch_enable_experimental --feature containsFilter
```

### Type Error on Non-String Value

**Error**: `TypeError: CONTAINS operator only supports string values, not int. ...`

This is raised when the **value** passed to `__contains` isn't a string —
`final_price__contains="99"` (a string value) does NOT raise; only a
non-string value like `final_price__contains=99` (an int) does.

**Solution**: Use appropriate lookup for the field type:
```python
# ❌ Wrong (non-string value)
ProductTranslation.meilisearch.filter(final_price__contains=99)

# ✅ Correct
ProductTranslation.meilisearch.filter(final_price__gte=99, final_price__lte=999)
```

### Slow Query Performance

**Problem**: CONTAINS queries are slow

**Solutions**:
1. Set searchCutoffMs timeout
2. Combine with other filters to narrow results
3. Use full-text search instead for general queries
4. Consider indexing strategy (smaller indexes perform better)

## References

- [Meilisearch CONTAINS Documentation](https://docs.meilisearch.com/learn/filtering_and_sorting/filter_expression_reference.html#contains)
- [Experimental Features](https://docs.meilisearch.com/learn/experimental/overview.html)
- [GrooveShop IndexQuerySet API](../meili/querysets.md)
- [Search API Documentation](../api/search.md)

## Support

For questions or issues with the CONTAINS operator:

- **GitHub Issues**: https://github.com/grooveshop/grooveshop-django-api/issues
- **Meilisearch Discord**: https://discord.gg/meilisearch
- **Email**: dev-support@grooveshop.com
