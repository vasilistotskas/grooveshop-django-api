# API Pagination Documentation

## Overview

The GrooveShop API supports multiple pagination strategies to provide flexibility for different client needs. All list endpoints support pagination with configurable strategies.

## Pagination Types

### 1. Page Number Pagination (Default)
- **Query Parameter**: `pagination_type=pageNumber`
- **Description**: Traditional page-based pagination
- **Additional Parameters**:
  - `page`: Page number (default: 1)
  - `page_size`: Items per page (default: 20, max: 100)

**Example Request**:
```
GET /api/products/?pagination_type=pageNumber&page=2&page_size=10
```

**Example Response**:
```json
{
  "links": {
    "next": "http://api.example.com/products/?page=3&page_size=10",
    "previous": "http://api.example.com/products/?page=1&page_size=10"
  },
  "count": 150,
  "total_pages": 15,
  "page_size": 10,
  "page_total_results": 10,
  "page": 2,
  "results": [...]
}
```

### 2. Cursor Pagination
- **Query Parameter**: `pagination_type=cursor`
- **Description**: Cursor-based pagination for consistent results during data changes
- **Additional Parameters**:
  - `cursor`: Cursor token for next/previous page
  - `page_size`: Items per page (default: 100, max: 100)

**Example Request**:
```
GET /api/products/?pagination_type=cursor&page_size=20
```

**Example Response**:
```json
{
  "links": {
    "next": "http://api.example.com/products/?cursor=cD0yMDIzLTEyLTE1KzAyJTNBMDA%3D",
    "previous": null
  },
  "count": 150,
  "total_pages": 8,
  "page_size": 20,
  "page_total_results": 20,
  "results": [...]
}
```

### 3. Limit/Offset Pagination
- **Query Parameter**: `pagination_type=limitOffset`
- **Description**: SQL-style limit/offset pagination
- **Additional Parameters**:
  - `limit`: Number of items to return (default: 20, max: 100)
  - `offset`: Number of items to skip (default: 0)

**Example Request**:
```
GET /api/products/?pagination_type=limitOffset&limit=15&offset=30
```

**Example Response**:
```json
{
  "links": {
    "next": "http://api.example.com/products/?limit=15&offset=45",
    "previous": "http://api.example.com/products/?limit=15&offset=15"
  },
  "count": 150,
  "total_pages": 10,
  "page_size": 15,
  "page_total_results": 15,
  "page": 3,
  "results": [...]
}
```

## Common Parameters

### Pagination Control
- `pagination`: Enable/disable pagination (`true`/`false`, default: `true`)
- `pagination_type`: Pagination strategy (`pageNumber`/`cursor`/`limitOffset`, default: `pageNumber`)
- `page_size`: Items per page (available for all types, max: 100)

### Disabling Pagination
To get all results without pagination:
```
GET /api/products/?pagination=false
```

## Usage Examples

### Frontend Integration

#### React/JavaScript Example
```javascript
const fetchProducts = async (paginationType = 'pageNumber', page = 1, pageSize = 20) => {
  const params = new URLSearchParams({
    pagination_type: paginationType,
    page: page.toString(),
    page_size: pageSize.toString()
  });

  const response = await fetch(`/api/products/?${params}`);
  return response.json();
};

// Usage
const data = await fetchProducts('pageNumber', 2, 10);
```

#### Nuxt.js/Vue Example
```typescript
// composables/useProducts.ts
export const useProducts = () => {
  const fetchProducts = async (options: {
    paginationType?: 'pageNumber' | 'cursor' | 'limitOffset';
    page?: number;
    pageSize?: number;
    cursor?: string;
  } = {}) => {
    const {
      paginationType = 'pageNumber',
      page = 1,
      pageSize = 20,
      cursor
    } = options;

    const query: Record<string, string> = {
      pagination_type: paginationType,
      page_size: pageSize.toString()
    };

    if (paginationType === 'cursor' && cursor) {
      query.cursor = cursor;
    } else if (paginationType !== 'cursor') {
      query.page = page.toString();
    }

    return await $fetch('/api/products/', { query });
  };

  return { fetchProducts };
};
```

## Best Practices

1. **Use Page Number Pagination** for user-facing interfaces with page controls
2. **Use Cursor Pagination** for real-time feeds or when data consistency is critical
3. **Use Limit/Offset Pagination** for data exports or when you need to jump to specific offsets
4. **Set appropriate page sizes** - smaller for mobile, larger for desktop
5. **Handle pagination parameters** in your frontend state management
6. **Cache pagination results** when appropriate to improve performance

## Error Handling

Invalid pagination parameters will return a 400 Bad Request with details:

```json
{
  "error": "Invalid pagination_type. Must be one of: pageNumber, cursor, limitOffset"
}
```

## Performance Considerations

- **Page Number**: Good performance for small to medium datasets
- **Cursor**: Best performance for large datasets and real-time data
- **Limit/Offset**: Can be slow for large offsets, use with caution

## Migration Guide

If you're currently using the default pagination, no changes are required. The API maintains backward compatibility while adding the new pagination options.

To migrate to a specific pagination type, simply add the `pagination_type` parameter to your requests.
