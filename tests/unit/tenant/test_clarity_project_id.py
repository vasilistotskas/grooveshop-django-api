"""``Tenant.clarity_project_id``: the store's Microsoft Clarity project.

The storefront puts the value in the script's URL, so a malformed id is
rejected at the edge. The serializer field must stay optional: a read-only
field is required in the schema, and a storefront deployed before this
backend would then reject every ``/tenant/resolve`` response.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from tenant.models import Tenant
from tenant.role_scopes import TENANT_SELF_EDITABLE_FIELDS
from tenant.serializers import TenantConfigSerializer


def _tenant(project_id: str) -> Tenant:
    return Tenant(
        schema_name="public",
        name="t",
        slug="t",
        owner_email="t@example.com",
        clarity_project_id=project_id,
    )


class ClarityProjectIdValidationTests(SimpleTestCase):
    def test_accepts_an_alphanumeric_id(self):
        _tenant("z8z79wsqeu")._validate_clarity_project_id()

    def test_accepts_empty_meaning_disabled(self):
        _tenant("")._validate_clarity_project_id()

    def test_rejects_what_a_copy_from_the_snippet_produces(self):
        for bad in (
            '"z8z79wsqeu"',
            "z8z79wsqeu;",
            "https://www.clarity.ms/tag/z8z79wsqeu",
            "z8z79 wsqeu",
        ):
            with self.subTest(project_id=bad):
                with self.assertRaises(ValidationError) as ctx:
                    _tenant(bad)._validate_clarity_project_id()
                self.assertIn("clarity_project_id", ctx.exception.message_dict)

    def test_clean_runs_the_validator(self):
        with self.assertRaises(ValidationError) as ctx:
            _tenant("not valid").clean()
        self.assertIn("clarity_project_id", ctx.exception.message_dict)


class ClarityProjectIdContractTests(SimpleTestCase):
    def test_serializer_field_is_optional_not_read_only(self):
        field = TenantConfigSerializer().fields["clarity_project_id"]
        self.assertFalse(field.read_only)
        self.assertFalse(field.required)

    def test_the_store_owner_may_set_it(self):
        self.assertIn("clarity_project_id", TENANT_SELF_EDITABLE_FIELDS)
