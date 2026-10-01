"""Unfold datasets: a paginated, searchable list inside a change form.

An inline renders every related row (``max_num`` caps new rows, not
the ones shown), so a product with years of stock movements rendered
all of them on each open. A dataset pages them instead.
"""

from __future__ import annotations

from admin.base import BaseModelAdmin


class RelatedDatasetAdmin(BaseModelAdmin):
    """The rows of ``model`` that belong to the change form's object.

    Unfold passes the object's id as ``extra_context["object"]`` (none
    on the add form). A reader without view permission on ``model``
    gets an empty list, as the model's own changelist would refuse.
    """

    parent_field: str
    # Set on the instance by ``unfold.datasets.BaseDataset``.
    extra_context: dict
    list_per_page = 20

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        object_id = self.extra_context.get("object")
        if not object_id or not self.has_view_permission(request):
            return queryset.none()
        return queryset.filter(**{self.parent_field: object_id})
