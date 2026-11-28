# Core Base Classes and Patterns

Reference for the shared abstract models, base viewsets, managers, filters, pagination, serializer helpers, permissions, and Unfold admin action rules in `src/core/` and `src/*/admin.py`.

**Abstract models** (all in `src/core/models.py`):
- `TimeStampMixinModel` — `created_at`/`updated_at` with indexes
- `UUIDModel` — UUID4 field for external/guest access
- `SeoTranslationModel` — `seo_title`, `seo_description`, `seo_keywords`, for a model's parler **translations** model (never the shared row): inherit it on an explicit `TranslatedFieldsModel`, or spread `**SeoTranslationModel.translated_fields()` into `TranslatedFields(...)`
- `SortableModel` — `sort_order` with atomic `move_up()`/`move_down()` using `select_for_update()`
- `PublishableModel` — `is_published`/`published_at` with `PublishableManager.published()` queryset
- `MetaDataModel` — `metadata`/`private_metadata` JSONFields with GinIndex
- `SoftDeleteModel` — `is_deleted`/`deleted_at` with `SoftDeleteManager` (`.all_with_deleted()` and `.deleted_only()` on the manager, `.restore()` and `.hard_delete()` on its querysets); the queryset and manager behaviour is `core.mixins.soft_delete` (`SoftDeleteQuerySetMixin`, `SoftDeleteManagerMixin`), so a bulk `queryset.delete()` soft-deletes as `instance.delete()` does and only `hard_delete()` removes rows

Domain models compose multiple mixins, e.g. `Product(SoftDeleteModel, TranslatableModel, TimeStampMixinModel, UUIDModel, MetaDataModel, TaggedModel)` with `ProductTranslation(TranslatedFieldsModel, SeoTranslationModel, IndexMixin)`.

**Base ViewSet** (`src/core/api/views.py: BaseModelViewSet`):
- Combines `RequestResponseSerializerMixin`, `TranslationsModelViewSet`, `PaginationModelViewSet`
- Uses `serializers_config` dict mapping actions to `ActionConfig` objects (separate request/response serializers per action)
- Supports three pagination strategies via `?pagination_type=` query param: `pageNumber` (default), `cursor`, `limitOffset`
- Handles multipart translation data via `TranslationsProcessingMixin`
- Atomic transactions on create/update
- URL confs wire actions by hand (`ViewSet.as_view({"post": "create_checkout_session"})`), not through a router; `RouterActionOverridesMixin` (in `BaseModelViewSet`) applies each extra action's `@action(...)` arguments — `throttle_classes`, `permission_classes`, `pagination_class`, `queryset` — the way DRF's router would. A viewset not built on `BaseModelViewSet` must include it; `tests/unit/core/test_routes_apply_action_overrides.py` fails for any route that drops an override

**Optimized Managers** (`src/core/managers/`):
- `OptimizedManager`/`OptimizedQuerySet` — Override `for_list()` and `for_detail()` for select_related/prefetch_related
- `TranslatableOptimizedManager`/`TranslatableOptimizedQuerySet` — Adds `with_translations()` for parler
- `TreeTranslatableManager`/`TreeTranslatableQuerySet` — For MPTT + Parler models (categories)
- ViewSets call `Model.objects.for_list()` or `.for_detail()` based on action

**Composable FilterSets** (`src/core/filters/`):
- Mixin-based (`src/core/filters/core.py`): `TimeStampFilterMixin`, `UUIDFilterMixin`, `SortableFilterMixin`, `MetaDataFilterMixin` — each a real `FilterSet` subclass, because plain-class mixins were silently dropped by django-filter (see the module docstring)
- CamelCase variants (`src/core/filters/camel_case_filters.py`): `CamelCaseFilterMixin` auto-converts query param names; bases `CamelCaseTimeStampFilterSet` and `CamelCasePublishableTimeStampFilterSet`

**Pagination** (`src/core/pagination/`): `PageNumberPaginator`, `CursorPaginator`, `LimitOffsetPaginator` — all return consistent envelope: `{links, count, total_pages, page_size, page_total_results, page, results}`

**Action config** (`src/core/api/action_config.py`; the shared serializer field `TranslatedFieldExtended` is in `src/core/utils/serializers.py`):
- `ActionConfig` dataclass — per-action request/response serializer + OpenAPI metadata
- `crud_config()` — shorthand for standard CRUD serializer mapping
- `create_schema_view_config()` — auto-generates `@extend_schema` decorators from model verbose names

**Translation validation** (`src/core/api/serializers.py`): `RequiredDefaultTranslationMixin` — mix into a `TranslatableModelSerializer` and set `required_translation_field` to require the `settings.PARLER_DEFAULT_LANGUAGE_CODE` translation (with that field non-empty); other locales stay optional. Never hardcode a language code (e.g. `if "en" in value`) — the default is `el` and the locale set can change.

**Custom fields**: `ImageAndSvgField` (images + SVG), `MeasurementField` (physical measurements with unit conversion)

**Permissions** (`src/core/api/permissions.py`): `IsOwnerOrAdmin`, `IsOwnerOrAdminOrGuest` — checks `user`, `owner`, or `created_by` fields; the staff bypass is `tenant.membership.is_store_staff` (a provenance-stamped platform identity with a staff role in the current tenant, or a superuser). `request.user.is_staff` is never an authorization signal on an API request — on a tenant-schema row it is customer residue (see `IsPlatformSuperuser`). Store-scoped write routes use `StoreStaffModelPermissions`. `IsOwnerOrAdminOrGuest` additionally handles guest orders: when `obj.user is None`, it verifies `request.query_params.get("uuid") == str(obj.uuid)`.

**Admin actions** (`src/*/admin.py`): every action passes `permissions=[...]` in its decorator: `change` for an edit, `add` for a copy, `delete` for a deletion, `view` only for a named read-only one. Unfold serves list, row and detail actions at URLs of their own, behind the admin login and that argument alone; `tests/unit/admin/test_actions_declare_permissions.py` fails for an action without one. A list, row or detail action confirms first and acts only on a POST: give it `dialog=ActionDialog(...)` (Unfold then runs the body only for the posted form, and the body answers with `admin.responses.hx_redirect`), or, if it is a download or a redirect, name it in its admin's `read_actions` (the ACS voucher download is one that also stamps the label as printed); `tests/unit/admin/test_actions_confirm.py` fails for any other. A detail action's `has_<perm>_permission` receives the object id (a string), never the object: look the row up by it (`OrderAdmin.has_confirm_payment_permission`). A package's admin on the store site belongs to an app in `tenant.role_scopes.PLATFORM_ONLY_APP_LABELS` and is named in that test's `THIRD_PARTY_ADMINS`; a URL it serves itself is named in `THIRD_PARTY_URLS` with its check (dj-stripe's: `admin.mixins.StripeMirrorModelAdmin`).
