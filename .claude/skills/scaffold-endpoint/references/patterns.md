# Scaffold Endpoint — Complete Pattern Reference

This file contains every pattern you need to scaffold a complete endpoint. Read only the sections relevant to the model being scaffolded.

## Table of Contents

1. [Model Patterns](#1-model-patterns)
2. [Manager Patterns](#2-manager-patterns)
3. [Serializer Patterns](#3-serializer-patterns)
4. [FilterSet Patterns](#4-filterset-patterns)
5. [ViewSet Patterns](#5-viewset-patterns)
6. [URL Patterns](#6-url-patterns)
7. [Admin Patterns](#7-admin-patterns)
8. [Factory Patterns](#8-factory-patterns)
9. [App Registration](#9-app-registration)
10. [Import Reference](#10-import-reference)

---

## 1. Model Patterns

### Available Abstract Mixins (from `core/models.py`)

| Mixin | Fields Added | When to Use |
|-------|-------------|-------------|
| `TimeStampMixinModel` | `created_at`, `updated_at` | Almost always |
| `UUIDModel` | `uuid` (UUID4, unique) | When external/guest access needed |
| `SeoModel` | `seo_title`, `seo_description`, `seo_keywords` | Public-facing content |
| `SortableModel` | `sort_order` (atomic move_up/move_down) | Ordered items |
| `PublishableModel` | `is_published`, `published_at` + `.published()` queryset | Time-sensitive content |
| `MetaDataModel` | `metadata`, `private_metadata` (JSONField + GIN index) | Extensible entities |
| `SoftDeleteModel` | `is_deleted`, `deleted_at` + `.all_with_deleted()` | Important entities (orders, products) |

### Standard Model Template

```python
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from parler.models import TranslatableModel, TranslatedFields

from core.models import TimeStampMixinModel, UUIDModel


class YourModel(
    TranslatableModel,       # Only if multilingual
    TimeStampMixinModel,     # Almost always
    UUIDModel,               # If needed for external access
):
    id = models.BigAutoField(primary_key=True)
    slug = models.SlugField(max_length=255, unique=True)
    # ... domain fields ...

    # Translations (only if TranslatableModel)
    translations = TranslatedFields(
        name=models.CharField(_("Name"), max_length=255),
        description=models.TextField(_("Description"), blank=True, default=""),
    )

    objects: YourModelManager = YourModelManager()

    class Meta(TypedModelMeta):
        verbose_name = _("Your Model")
        verbose_name_plural = _("Your Models")
        ordering = ["-created_at"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            # Add model-specific indexes
        ]

    def __str__(self):
        # For translatable models:
        return self.safe_translation_getter("name", any_language=True) or ""
        # For non-translatable:
        # return self.name
```

### Computed Property Pattern (avoids N+1)

When a model needs a count or aggregate that may come from annotation:

```python
@property
def likes_count(self) -> int:
    if "likes_count" in self.__dict__:
        return self.__dict__["likes_count"]
    return self.likes.count()

@likes_count.setter
def likes_count(self, value: int) -> None:
    self.__dict__["likes_count"] = value
```

### Index Conventions

- Always spread parent mixin indexes: `*TimeStampMixinModel.Meta.indexes`
- Use `BTreeIndex` from `django.contrib.postgres.indexes` for standard fields
- Use `GinIndex` for JSONField (MetaDataModel does this automatically)
- Name format: `{app}_{model}_{field}_ix` (e.g., `product_product_slug_ix`)

---

## 2. Manager Patterns

### For Non-Translatable Models

```python
from core.managers.base import OptimizedManager, OptimizedQuerySet


class YourModelQuerySet(OptimizedQuerySet):
    def with_related_entity(self):
        return self.select_related("related_fk")

    def with_many_related(self):
        return self.prefetch_related("many_to_many")

    def for_list(self):
        return self.with_related_entity()

    def for_detail(self):
        return self.for_list().with_many_related()


class YourModelManager(OptimizedManager):
    queryset_class = YourModelQuerySet
```

### For Translatable Models

```python
from core.managers.base import (
    TranslatableOptimizedManager,
    TranslatableOptimizedQuerySet,
)


class YourModelQuerySet(TranslatableOptimizedQuerySet):
    def with_related_entity(self):
        return self.select_related("related_fk")

    def for_list(self):
        return self.with_translations().with_related_entity()

    def for_detail(self):
        return self.for_list().prefetch_related("images", "tags")


class YourModelManager(TranslatableOptimizedManager):
    queryset_class = YourModelQuerySet
```

### For Tree + Translatable Models (MPTT categories)

```python
from core.managers.tree import TreeTranslatableManager, TreeTranslatableQuerySet


class YourCategoryQuerySet(TreeTranslatableQuerySet):
    def for_list(self):
        return self.with_translations().with_parent()

    def for_detail(self):
        return self.for_list()


class YourCategoryManager(TreeTranslatableManager):
    queryset_class = YourCategoryQuerySet
```

### For Soft-Deletable Models

Add `SoftDeleteQuerySetMixin` from `core.mixins.queryset`:

```python
from core.managers.base import TranslatableOptimizedManager, TranslatableOptimizedQuerySet
from core.mixins.queryset import SoftDeleteQuerySetMixin


class YourModelQuerySet(SoftDeleteQuerySetMixin, TranslatableOptimizedQuerySet):
    def active(self):
        return self.filter(active=True).exclude_deleted()

    def for_list(self):
        return self.active().with_translations()

    def for_detail(self):
        return self.exclude_deleted().with_translations()
```

### Annotation Methods (for counts/aggregates)

```python
from django.db.models import Avg, Count, F, Q, Value
from django.db.models.functions import Coalesce


class YourModelQuerySet(TranslatableOptimizedQuerySet):
    def with_likes_count(self):
        return self.annotate(likes_count=Count("likes", distinct=True))

    def with_review_average(self):
        return self.annotate(
            review_average=Coalesce(
                Avg("reviews__rate"),
                Value(0.0, output_field=models.FloatField()),
            )
        )

    def with_counts(self):
        return self.with_likes_count().with_review_average()

    def for_list(self):
        return self.with_translations().with_counts()
```

---

## 3. Serializer Patterns

### Three-Tier Structure

Every model exposed via API gets three serializers:

1. **List** (`{Model}Serializer`) — minimal fields for collection endpoints
2. **Detail** (`{Model}DetailSerializer`) — extends List with extra fields
3. **Write** (`{Model}WriteSerializer`) — validation-focused, for create/update

### TranslatedFieldsFieldExtend (define per serializer module)

Every serializer module that handles translatable models defines a local subclass. This is the established pattern throughout the codebase:

```python
from drf_spectacular.utils import extend_schema_field
from core.api.schema import generate_schema_multi_lang
from core.utils.serializers import TranslatedFieldExtended

@extend_schema_field(generate_schema_multi_lang(YourModel))
class TranslatedFieldsFieldExtend(TranslatedFieldExtended):
    pass
```

### List Serializer (Translatable)

```python
from rest_framework import serializers
from parler_rest.serializers import TranslatableModelSerializer
from drf_spectacular.utils import extend_schema_field

from core.api.schema import generate_schema_multi_lang
from core.utils.serializers import TranslatedFieldExtended
from your_app.models import YourModel


@extend_schema_field(generate_schema_multi_lang(YourModel))
class TranslatedFieldsFieldExtend(TranslatedFieldExtended):
    pass


class YourModelSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer[YourModel]
):
    translations = TranslatedFieldsFieldExtend(shared_model=YourModel)

    class Meta:
        model = YourModel
        fields = (
            "id",
            "translations",
            "slug",
            "created_at",
            "updated_at",
            "uuid",
        )
        read_only_fields = (
            "id",
            "created_at",
            "updated_at",
            "uuid",
        )
```

### List Serializer (Non-Translatable)

```python
class YourModelSerializer(serializers.ModelSerializer[YourModel]):
    class Meta:
        model = YourModel
        fields = (
            "id",
            "name",
            "slug",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "created_at",
            "updated_at",
        )
```

### Detail Serializer

```python
class YourModelDetailSerializer(YourModelSerializer):
    # Add extra fields, nested serializers, or method fields
    related_items = RelatedItemSerializer(many=True, read_only=True)

    class Meta(YourModelSerializer.Meta):
        fields = (
            *YourModelSerializer.Meta.fields,
            "related_items",
            "seo_title",
            "seo_description",
            "seo_keywords",
        )
        read_only_fields = (
            *YourModelSerializer.Meta.read_only_fields,
            "related_items",
        )
```

### Write Serializer

```python
class YourModelWriteSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer[YourModel]
):
    translations = TranslatedFieldsFieldExtend(shared_model=YourModel)  # Reuse from same module
    # Use PrimaryKeyRelatedField for ForeignKeys in write
    category = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.all()
    )

    class Meta:
        model = YourModel
        fields = (
            "translations",
            "slug",
            "category",
            # ... writable fields only
        )

    def validate_slug(self, value):
        if not value:
            raise serializers.ValidationError(_("Slug is required."))
        queryset = YourModel.objects.filter(slug=value)
        if self.instance:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise serializers.ValidationError(
                _("An item with this slug already exists.")
            )
        return value
```

### SerializerMethodField with Annotation Fallback

```python
class YourModelSerializer(TranslatableModelSerializer, serializers.ModelSerializer):
    item_count = serializers.SerializerMethodField()

    def get_item_count(self, obj) -> int:
        if hasattr(obj, "_item_count"):
            return obj._item_count
        return obj.items.count()
```

### SerializerMethodField with OpenAPI Schema

```python
from drf_spectacular.utils import extend_schema_field


class YourModelDetailSerializer(YourModelSerializer):
    category = serializers.SerializerMethodField()

    @extend_schema_field(CategorySerializer())
    def get_category(self, obj):
        return CategorySerializer(obj.category, context=self.context).data
```

### Custom Action Serializers

For non-CRUD actions that need their own request/response shape:

```python
class YourActionRequestSerializer(serializers.Serializer):
    item_ids = serializers.ListField(
        child=serializers.IntegerField(),
        help_text=_("List of item IDs"),
    )

class YourActionResponseSerializer(serializers.Serializer):
    item_ids = serializers.ListField(
        child=serializers.IntegerField(),
        help_text=_("List of matching item IDs"),
    )
```

### Special Fields

```python
from djmoney.contrib.django_rest_framework import MoneyField
from core.api.serializers import MeasurementSerializerField

# Money
price = MoneyField(max_digits=11, decimal_places=2)

# Measurement (weight, dimensions)
weight = MeasurementSerializerField(measurement=Weight, unit_choices=WeightUnits.CHOICES)

# Image + SVG
from core.fields.image import ImageAndSvgField
image = ImageAndSvgField(upload_to="uploads/your_app/")
```

---

## 4. FilterSet Patterns

### Available Core Mixins (from `core/filters/`)

| Mixin | Filters Added | For Models With |
|-------|--------------|-----------------|
| `TimeStampFilterMixin` | `created_after/before`, `updated_after/before` | `TimeStampMixinModel` |
| `UUIDFilterMixin` | `uuid` | `UUIDModel` |
| `SortableFilterMixin` | `sort_order`, `sort_order_min/max` | `SortableModel` |
| `PublishableFilterMixin` | `is_published`, `published_after/before`, `currently_published` | `PublishableModel` |
| `SoftDeleteFilterMixin` | `is_deleted`, `include_deleted`, `deleted_after/before` | `SoftDeleteModel` |
| `MetaDataFilterMixin` | `metadata_has_key`, `metadata_contains`, etc. | `MetaDataModel` |

### Pre-built Combinations

| FilterSet | Mixins Included |
|-----------|----------------|
| `BaseTimeStampFilterSet` | TimeStamp |
| `BasePublishableTimeStampFilterSet` | Publishable + TimeStamp |
| `BaseSoftDeleteTimeStampFilterSet` | SoftDelete + TimeStamp |
| `BaseFullFilterSet` | All mixins |
| `CamelCaseTimeStampFilterSet` | TimeStamp + CamelCase auto-convert |
| `CamelCasePublishableTimeStampFilterSet` | Publishable + TimeStamp + CamelCase |

### Standard FilterSet Template

```python
from django_filters import rest_framework as filters
from django.utils.translation import gettext_lazy as _

from core.filters.camel_case_filters import CamelCaseTimeStampFilterSet
from core.filters.core import UUIDFilterMixin
from your_app.models import YourModel


class YourModelFilter(UUIDFilterMixin, CamelCaseTimeStampFilterSet):
    # Custom filters for domain-specific fields
    name = filters.CharFilter(
        field_name="translations__name",
        lookup_expr="icontains",
        help_text=_("Filter by name (case-insensitive)"),
    )
    category = filters.ModelChoiceFilter(
        field_name="category",
        queryset=None,  # Set in __init__
    )
    min_price = filters.NumberFilter(
        field_name="price",
        lookup_expr="gte",
        help_text=_("Minimum price"),
    )
    max_price = filters.NumberFilter(
        field_name="price",
        lookup_expr="lte",
        help_text=_("Maximum price"),
    )

    class Meta:
        model = YourModel
        fields = {
            "id": ["exact"],
            "slug": ["exact", "icontains"],
            "created_at": ["gte", "lte", "date"],
            "updated_at": ["gte", "lte", "date"],
            "uuid": ["exact"],
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from your_app.models import Category
        self.filters["category"].queryset = Category.objects.all()
```

### Method Filter Pattern (with queryset annotations)

```python
min_likes = filters.NumberFilter(
    method="filter_min_likes",
    help_text=_("Minimum likes count"),
)

def filter_min_likes(self, queryset, name, value):
    if value is not None:
        return queryset.with_likes_count().filter(likes_count__gte=value)
    return queryset
```

---

## 5. ViewSet Patterns

### Standard ViewSet Template

```python
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_view
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet
from core.utils.serializers import (
    ActionConfig,
    SerializersConfig,
    create_schema_view_config,
    crud_config,
)
from your_app.filters import YourModelFilter
from your_app.models import YourModel
from your_app.serializers import (
    YourModelDetailSerializer,
    YourModelSerializer,
    YourModelWriteSerializer,
)

# 1. Define serializers_config OUTSIDE the class
serializers_config: SerializersConfig = {
    **crud_config(
        list=YourModelSerializer,
        detail=YourModelDetailSerializer,
        write=YourModelWriteSerializer,
    ),
    # Custom actions (if any):
    # "custom_action": ActionConfig(
    #     response=YourModelDetailSerializer,
    #     operation_id="customActionName",
    #     summary=_("Description of the action"),
    #     description=_("Longer description for OpenAPI docs."),
    #     tags=["Your Models"],
    # ),
}


# 2. Apply schema decorator
@extend_schema_view(
    **create_schema_view_config(
        model_class=YourModel,
        display_config={"tag": "Your Models"},
        serializers_config=serializers_config,
        error_serializer=ErrorResponseSerializer,
    )
)
class YourModelViewSet(BaseModelViewSet):
    queryset = YourModel.objects.all()
    serializers_config = serializers_config
    filterset_class = YourModelFilter

    ordering_fields = ["id", "created_at", "updated_at"]
    ordering = ["-created_at"]
    search_fields = ["translations__name", "slug"]  # For ?search= param

    def get_queryset(self):
        if self.action == "list":
            return YourModel.objects.for_list()
        return YourModel.objects.for_detail()
```

### Caching Pattern

```python
from core.utils.views import cache_methods
from django.conf import settings


@extend_schema_view(...)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class YourModelViewSet(BaseModelViewSet):
    ...
```

### Custom Action Patterns

**Detail action (operates on single object):**

```python
@action(detail=True, methods=["POST"])
def update_view_count(self, request, pk=None):
    instance = self.get_object()
    instance.view_count += 1
    instance.save(update_fields=["view_count"])

    response_serializer_class = self.get_response_serializer()
    response_serializer = response_serializer_class(
        instance, context=self.get_serializer_context()
    )
    return Response(response_serializer.data, status=status.HTTP_200_OK)
```

**List action (operates on queryset, paginated):**

```python
@action(detail=True, methods=["GET"])
def reviews(self, request, pk=None):
    instance = self.get_object()
    queryset = instance.reviews.all()

    response_serializer_class = self.get_response_serializer()
    return self.paginate_and_serialize(
        queryset, request, serializer_class=response_serializer_class
    )
```

**Non-paginated list action:**

```python
@action(detail=False, methods=["GET"], pagination_class=None, filter_backends=[])
def all(self, request):
    queryset = self.get_queryset()
    serializer = YourModelSerializer(
        queryset, many=True, context=self.get_serializer_context()
    )
    return Response(serializer.data)
```

**Action with request + response serializers:**

```python
@action(detail=False, methods=["POST"])
def check_items(self, request):
    request_serializer_class = self.get_request_serializer()
    request_serializer = request_serializer_class(data=request.data)
    if not request_serializer.is_valid():
        return Response(
            request_serializer.errors, status=status.HTTP_400_BAD_REQUEST
        )

    item_ids = request_serializer.validated_data["item_ids"]
    # ... business logic ...

    response_serializer_class = self.get_response_serializer()
    response_serializer = response_serializer_class({"item_ids": result_ids})
    return Response(response_serializer.data, status=status.HTTP_200_OK)
```

### Dynamic FilterSet (skip for custom actions)

```python
def get_filterset_class(self):
    if self.action in ["reviews", "images", "tags"]:
        return None
    return YourModelFilter

@property
def filterset_class(self):
    return self.get_filterset_class()
```

### Action-Based Permissions

```python
from rest_framework.permissions import IsAdminUser
from core.api.permissions import IsOwnerOrAdmin, IsOwnerOrAdminOrGuest


def get_permissions(self):
    owner_actions = {"list", "retrieve", "update", "partial_update", "destroy"}
    admin_actions = {"bulk_delete", "export"}
    public_actions = {"create"}

    if self.action in owner_actions:
        self.permission_classes = [IsOwnerOrAdmin]
    elif self.action in admin_actions:
        self.permission_classes = [IsAdminUser]
    elif self.action in public_actions:
        self.permission_classes = []
    else:
        self.permission_classes = [IsAdminUser]

    return super().get_permissions()
```

---

## 6. URL Patterns

This project uses **manual path() registration**, NOT DRF routers.

### Standard CRUD URLs

```python
from django.urls import path
from rest_framework.urlpatterns import format_suffix_patterns

from your_app.views import YourModelViewSet

urlpatterns = [
    # List + Create
    path(
        "your-model",
        YourModelViewSet.as_view({"get": "list", "post": "create"}),
        name="your-model-list",
    ),
    # Retrieve + Update + Delete
    path(
        "your-model/<int:pk>",
        YourModelViewSet.as_view({
            "get": "retrieve",
            "put": "update",
            "patch": "partial_update",
            "delete": "destroy",
        }),
        name="your-model-detail",
    ),
]

urlpatterns = format_suffix_patterns(urlpatterns)
```

### Custom Action URLs

```python
# Detail action (with pk)
path(
    "your-model/<int:pk>/reviews",
    YourModelViewSet.as_view({"get": "reviews"}),
    name="your-model-reviews",
),

# Non-detail action (no pk)
path(
    "your-model/trending",
    YourModelViewSet.as_view({"get": "trending"}),
    name="your-model-trending",
),

# POST action on detail
path(
    "your-model/<int:pk>/update_view_count",
    YourModelViewSet.as_view({"post": "update_view_count"}),
    name="your-model-update-view-count",
),
```

### URL Naming Convention

- List: `{entity}-list`
- Detail: `{entity}-detail`
- Custom actions: `{entity}-{action-name}` (kebab-case)

### Nested Resource URLs (e.g., blog posts under blog/)

```python
# Blog app uses path prefix
path("blog/post", BlogPostViewSet.as_view({...}), name="blog-post-list"),
path("blog/post/<int:pk>", BlogPostViewSet.as_view({...}), name="blog-post-detail"),
path("blog/category", BlogCategoryViewSet.as_view({...}), name="blog-category-list"),
```

### Core URL Registration

In `core/urls.py`, new apps are registered under `api/v1/`:

```python
path("api/v1/", include("your_app.urls")),
```

---

## 7. Admin Patterns

```python
from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline

from your_app.models import YourModel


class YourModelInline(TabularInline):
    model = RelatedModel
    extra = 1


@admin.register(YourModel)
class YourModelAdmin(ModelAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_fullwidth = True
    list_filter_submit = True

    list_display = ["id", "name", "created_at"]
    list_filter = ["created_at"]
    search_fields = ["translations__name", "slug"]
    readonly_fields = ["id", "created_at", "updated_at", "uuid"]
    inlines = [YourModelInline]
```

For translatable models, also inherit `TranslatableAdmin` from `parler.admin`.

---

## 8. Factory Patterns

### Standard Translatable Factory

```python
import factory
from django.apps import apps
from faker import Faker

from devtools.factories import CustomDjangoModelFactory
from your_app.models import YourModel

fake = Faker()
available_languages = ["el", "en", "de"]


class YourModelTranslationFactory(factory.django.DjangoModelFactory):
    language_code = factory.Iterator(available_languages)
    name = factory.Faker("word")
    description = factory.Faker("text", max_nb_chars=200)
    master = factory.SubFactory(
        "your_app.factories.YourModelFactory"
    )

    class Meta:
        model = apps.get_model("your_app", "YourModelTranslation")
        django_get_or_create = ("language_code", "master")


class YourModelFactory(CustomDjangoModelFactory):
    auto_translations = False

    unique_model_fields = [
        ("slug", lambda: fake.slug()),
    ]

    class Meta:
        model = YourModel
        django_get_or_create = ("slug",)
        skip_postgeneration_save = True

    @factory.post_generation
    def translations(self, create, extracted, **kwargs):
        if not create:
            return
        translations = extracted or [
            YourModelTranslationFactory(language_code=lang, master=self)
            for lang in available_languages
        ]
        for translation in translations:
            translation.master = self
            translation.save()
```

### Non-Translatable Factory

```python
class YourModelFactory(CustomDjangoModelFactory):
    name = factory.Faker("word")
    slug = factory.Faker("slug")

    unique_model_fields = [
        ("slug", lambda: fake.slug()),
    ]

    class Meta:
        model = YourModel
        django_get_or_create = ("slug",)
        skip_postgeneration_save = True
```

### Smart Get-or-Create for ForeignKeys

```python
import importlib
from django.apps import apps


def get_or_create_category():
    Model = apps.get_model("your_app", "YourCategory")
    if Model.objects.exists():
        return Model.objects.order_by("?").first()
    else:
        factory_module = importlib.import_module("your_app.factories.category")
        return factory_module.YourCategoryFactory.create()
```

---

## 9. App Registration

### settings.py

Add to `LOCAL_APPS` list in `settings.py`:

```python
LOCAL_APPS = [
    # ... existing apps ...
    "your_app",
]
```

### apps.py

```python
from django.apps import AppConfig


class YourAppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "your_app"

    def ready(self):
        from . import signals  # noqa: F401
```

### core/urls.py

Add URL inclusion inside `i18n_patterns(...)`:

```python
path("api/v1/", include("your_app.urls")),
```

---

## 10. Import Reference

### Core Base Classes

```python
# Models
from core.models import (
    TimeStampMixinModel,
    UUIDModel,
    SeoModel,
    SortableModel,
    PublishableModel,
    MetaDataModel,
    SoftDeleteModel,
)

# Views
from core.api.views import BaseModelViewSet

# Serializers
from core.api.serializers import ErrorResponseSerializer
from core.api.schema import generate_schema_multi_lang
from core.utils.serializers import (
    ActionConfig,
    SerializersConfig,
    TranslatedFieldExtended,
    create_schema_view_config,
    crud_config,
)

# Managers
from core.managers.base import (
    OptimizedManager,
    OptimizedQuerySet,
    TranslatableOptimizedManager,
    TranslatableOptimizedQuerySet,
)
from core.managers.tree import TreeTranslatableManager, TreeTranslatableQuerySet
from core.mixins.queryset import SoftDeleteQuerySetMixin

# Filters
from core.filters.core import (
    TimeStampFilterMixin,
    UUIDFilterMixin,
    SortableFilterMixin,
    PublishableFilterMixin,
    SoftDeleteFilterMixin,
    MetaDataFilterMixin,
    BaseTimeStampFilterSet,
    BasePublishableTimeStampFilterSet,
    BaseSoftDeleteTimeStampFilterSet,
    BaseFullFilterSet,
)
from core.filters.camel_case_filters import (
    CamelCaseFilterMixin,
    CamelCaseTimeStampFilterSet,
    CamelCasePublishableTimeStampFilterSet,
)

# Permissions
from core.api.permissions import IsOwnerOrAdmin, IsOwnerOrAdminOrGuest

# Caching
from core.utils.views import cache_methods

# Factories
from devtools.factories import CustomDjangoModelFactory
```

### Third-Party

```python
# DRF
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAdminUser

# Parler (translations)
from parler.models import TranslatableModel, TranslatedFields
from parler_rest.serializers import TranslatableModelSerializer

# OpenAPI
from drf_spectacular.utils import extend_schema_view, extend_schema_field

# Filters
from django_filters import rest_framework as filters

# MPTT (tree models)
from mptt.models import MPTTModel, TreeForeignKey

# Money
from djmoney.models.fields import MoneyField
from djmoney.contrib.django_rest_framework import MoneyField as MoneySerializerField

# History
from simple_history.models import HistoricalRecords

# Admin
from unfold.admin import ModelAdmin, TabularInline
from parler.admin import TranslatableAdmin

# Factory
import factory
from faker import Faker
```
