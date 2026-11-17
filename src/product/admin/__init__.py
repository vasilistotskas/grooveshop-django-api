"""The product app's admin: one module per model admin.

Django's admin autodiscovery imports only this package, so it imports the
modules whose ``@admin.register`` decorators fill the registry, in the order
the models appear in the admin.
"""

from product.admin import (  # noqa: F401
    attribute,
    attribute_value,
    brand,
    category,
    category_image,
    favourite,
    image,
    product,
    review,
    variant_group,
)
