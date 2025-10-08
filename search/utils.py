"""Utility functions for multilanguage search operations."""

from typing import Any
from urllib.parse import unquote

from django.conf import settings

from blog.models.post import BlogPostTranslation
from product.models.product import ProductTranslation


def search_products(
    query: str,
    language_code: str | None = None,
    limit: int = 10,
    offset: int = 0,
    **filters: Any,
) -> dict:
    """Search products with language filtering and additional filters.

    Args:
        query: Search query string
        language_code: Language code to filter results (e.g., 'en', 'el', 'de')
        limit: Maximum number of results to return
        offset: Number of results to skip
        **filters: Additional filters (e.g., likes_count__gte=10)

    Returns:
        Dictionary with search results and metadata

    Example:
        >>> search_products(
        ...     "laptop",
        ...     language_code="en",
        ...     limit=20,
        ...     likes_count__gte=5
        ... )
    """
    decoded_query = unquote(query)

    search_qs = ProductTranslation.meilisearch.paginate(
        limit=limit, offset=offset
    )

    if language_code:
        filters["language_code"] = language_code
        search_qs = search_qs.locales(language_code)

    if filters:
        search_qs = search_qs.filter(**filters)

    return search_qs.search(q=decoded_query)


def search_blog_posts(
    query: str,
    language_code: str | None = None,
    limit: int = 10,
    offset: int = 0,
    **filters: Any,
) -> dict:
    """Search blog posts with language filtering and additional filters.

    Args:
        query: Search query string
        language_code: Language code to filter results (e.g., 'en', 'el', 'de')
        limit: Maximum number of results to return
        offset: Number of results to skip
        **filters: Additional filters (e.g., likes_count__gte=10)

    Returns:
        Dictionary with search results and metadata

    Example:
        >>> search_blog_posts(
        ...     "python tutorial",
        ...     language_code="en",
        ...     limit=20
        ... )
    """
    decoded_query = unquote(query)

    search_qs = BlogPostTranslation.meilisearch.paginate(
        limit=limit, offset=offset
    )

    if language_code:
        filters["language_code"] = language_code
        search_qs = search_qs.locales(language_code)

    if filters:
        search_qs = search_qs.filter(**filters)

    return search_qs.search(q=decoded_query)


def get_supported_languages() -> list[dict[str, str]]:
    """Get list of supported languages for search.

    Returns:
        List of dictionaries with language code and name

    Example:
        >>> get_supported_languages()
        [
            {'code': 'el', 'name': 'Greek'},
            {'code': 'en', 'name': 'English'},
            {'code': 'de', 'name': 'German'}
        ]
    """
    return [
        {"code": code, "name": str(name)} for code, name in settings.LANGUAGES
    ]


def search_all_content(
    query: str,
    language_code: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Search across all content types (products and blog posts).

    Args:
        query: Search query string
        language_code: Language code to filter results
        limit: Maximum number of results per content type

    Returns:
        Dictionary with results from all content types

    Example:
        >>> search_all_content("laptop", language_code="en", limit=5)
        {
            'products': {...},
            'blog_posts': {...},
            'total_hits': 15
        }
    """
    products = search_products(query, language_code=language_code, limit=limit)
    blog_posts = search_blog_posts(
        query, language_code=language_code, limit=limit
    )

    return {
        "products": products,
        "blog_posts": blog_posts,
        "total_hits": (
            products["estimated_total_hits"]
            + blog_posts["estimated_total_hits"]
        ),
    }


def validate_language_code(language_code: str) -> bool:
    """Validate if a language code is supported.

    Args:
        language_code: Language code to validate

    Returns:
        True if language is supported, False otherwise

    Example:
        >>> validate_language_code("en")
        True
        >>> validate_language_code("fr")
        False
    """
    supported_codes = [code for code, _ in settings.LANGUAGES]
    return language_code in supported_codes


def get_search_suggestions(
    query: str, language_code: str | None = None, limit: int = 5
) -> list[str]:
    """Get search suggestions based on query.

    This can be used for autocomplete functionality.

    Args:
        query: Partial search query
        language_code: Language code to filter suggestions
        limit: Maximum number of suggestions

    Returns:
        List of suggested search terms

    Example:
        >>> get_search_suggestions("lap", language_code="en", limit=5)
        ['laptop', 'laptop bag', 'laptop stand', ...]
    """
    results = search_products(
        query, language_code=language_code, limit=limit, offset=0
    )

    suggestions = []
    for result in results["results"]:
        obj = result["object"]
        if hasattr(obj, "name") and obj.name not in suggestions:
            suggestions.append(obj.name)

    return suggestions[:limit]
