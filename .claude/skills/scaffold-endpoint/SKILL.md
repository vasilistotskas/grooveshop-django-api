---
name: scaffold-endpoint
description: >
  Scaffold a complete DRF API endpoint for the GrooveShop Django API following
  all project conventions: BaseModelViewSet, ActionConfig, serializer tiering,
  optimized managers, composable FilterSets, manual URL routing, and admin
  registration. Use this skill whenever the user wants to create a new API
  endpoint, add a new resource/entity, scaffold views/serializers/filters for
  an existing model, or says things like "add an endpoint for X", "create CRUD
  for Y", "scaffold API for Z". Also use it when adding a new Django app that
  needs API endpoints.
disable-model-invocation: true
---

# Scaffold Endpoint

Generate a complete DRF API endpoint that matches every convention in this codebase.

## Before You Start

1. Ask the user which **model** they want to expose (existing or new).
2. Ask which **app** it belongs to (existing or new).
3. Ask what **actions** are needed beyond standard CRUD (custom `@action` endpoints).
4. Ask about **permissions** — public, authenticated, owner-only, admin-only, or mixed per-action.
5. Ask if the model is **translatable** (django-parler), **soft-deletable**, **publishable**, etc.

If the model doesn't exist yet, scaffold the model + manager first, then the endpoint.

## Workflow

1. **Model & Manager** (if new model)
2. **Serializers** (List / Detail / Write tiering)
3. **FilterSet**
4. **ViewSet**
5. **URLs**
6. **Admin registration**
7. **Factory** (for test data)
8. Register app in `settings.py` (if new app)
9. Register URLs in `core/urls.py` (if new app)

For detailed patterns for each step, read `references/patterns.md`.

## Key Conventions Quick Reference

- **Base ViewSet**: Always inherit `BaseModelViewSet` from `core.api.views`
- **Serializer config**: Use `crud_config()` for standard CRUD, extend dict for custom actions
- **Schema generation**: Always use `@extend_schema_view(**create_schema_view_config(...))`
- **URL routing**: Manual `path()` registration — NOT DRF routers
- **Managers**: Always define `for_list()` and `for_detail()` on a custom QuerySet
- **Filters**: Compose from core mixins (`TimeStampFilterMixin`, `UUIDFilterMixin`, etc.)
- **Admin**: Use django-unfold's `ModelAdmin` and `TabularInline`
- **Translations**: Define local `TranslatedFieldsFieldExtend(TranslatedFieldExtended)` per serializer module (see patterns.md §3)
- **Files**: Use directories (not single files) when an app has 3+ models/serializers/views

## File Placement Rules

**Simple app** (1-2 models): single files — `models.py`, `serializers.py`, `views.py`, `factories.py`, `filters.py`

**Complex app** (3+ models): directories with `__init__.py` — `models/`, `serializers/`, `views/`, `factories/`, `filters/`, `managers/`

Always create:
- `__init__.py`, `admin.py`, `apps.py`, `urls.py`, `migrations/__init__.py`

## Output Checklist

After scaffolding, verify:
- [ ] Model inherits appropriate mixins (TimeStampMixinModel, UUIDModel, etc.)
- [ ] Manager has `for_list()` and `for_detail()` with proper select_related/prefetch_related
- [ ] Three serializer tiers exist (List, Detail, Write)
- [ ] ViewSet uses `serializers_config` with `crud_config()` + any custom ActionConfigs
- [ ] `@extend_schema_view(**create_schema_view_config(...))` decorator is on ViewSet
- [ ] URLs use manual `path()` with `ViewSet.as_view({...})` mapping
- [ ] FilterSet composes appropriate core mixins
- [ ] Admin is registered with django-unfold's `ModelAdmin`
- [ ] Factory extends `CustomDjangoModelFactory` with proper translation handling
- [ ] App is in `LOCAL_APPS` in `settings.py`
- [ ] URLs are included in `core/urls.py` under `api/v1/`
