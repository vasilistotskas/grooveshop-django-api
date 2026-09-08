"""``Tenant.openai_pixel_id`` — the ChatGPT Ads conversion pixel id.

Two things are worth pinning, and neither is about the happy path.

**The validator.** The value the site owner supplied arrived pasted out
of OpenAI's own snippet, and the snippet's SDK URL came with a stray
trailing character (``…/oaiq.min.js^``). A malformed pixel id does not
fail loudly — conversions simply stop being attributed — so it is
rejected at the edge, exactly like the TikTok id.

**The serializer field must be OPTIONAL, not read-only.** This is the
one that can take the platform down. drf-spectacular marks every
read-only field as ``required`` in the schema, so the storefront's
generated Zod would reject any ``/tenant/resolve`` response from a
backend that predates the field. Argo rolls the frontend and backend as
separate Deployments, so a frontend-first deploy would fail
tenant-config validation for EVERY tenant and 503 the whole storefront
— the same failure shape as the stale-tenant-config incident on
2026-08-31.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.test import TestCase

from tenant.models import Tenant
from tenant.serializers import TenantConfigSerializer


class OpenAIPixelIdValidationTests(TestCase):
    def _tenant(self, pixel_id: str) -> Tenant:
        return Tenant(
            schema_name="public",
            name="t",
            slug="t",
            owner_email="t@example.com",
            openai_pixel_id=pixel_id,
        )

    def test_accepts_an_alphanumeric_id(self):
        self._tenant("8MktrqpXN1MRdD2NUfkXmU")._validate_openai_pixel_id()

    def test_accepts_empty_meaning_disabled(self):
        # Empty is how a tenant opts out; it must not raise, and it also
        # keeps bzrcdn.openai.com out of that tenant's CSP.
        self._tenant("")._validate_openai_pixel_id()

    def test_rejects_non_alphanumeric(self):
        # The trailing-character case that actually happened, plus the
        # other shapes a copy-paste produces.
        for bad in (
            "8MktrqpXN1MRdD2NUfkXmU^",
            "https://bzrcdn.openai.com/sdk/oaiq.min.js",
            "8Mktrqp XN1MRdD2NUfkXmU",
            "8Mktrqp-XN1MRdD2NUfkXmU",
            "8Mktrqp_XN1MRdD2NUfkXmU",
        ):
            with self.subTest(pixel_id=bad):
                with self.assertRaises(ValidationError) as ctx:
                    self._tenant(bad)._validate_openai_pixel_id()
                self.assertIn("openai_pixel_id", ctx.exception.message_dict)

    def test_full_clean_runs_the_validator(self):
        # The validator is only useful if it is actually wired into
        # clean(); a method nobody calls protects nothing.
        tenant = self._tenant("bad^id")
        with self.assertRaises(ValidationError) as ctx:
            tenant.clean()
        self.assertIn("openai_pixel_id", ctx.exception.message_dict)


class OpenAIPixelIdContractTests(TestCase):
    def test_field_is_optional_not_required(self):
        field = TenantConfigSerializer().fields["openai_pixel_id"]

        # If this ever flips to read_only/required, a frontend-first
        # deploy 503s every tenant. See the module docstring.
        self.assertFalse(
            field.read_only,
            "openai_pixel_id must NOT be read_only — drf-spectacular "
            "would emit it as required and break frontend-first deploys",
        )
        self.assertFalse(field.required)

    def test_field_reads_the_model_attribute(self):
        # Reads through the declared field rather than the whole
        # serializer: several sibling SerializerMethodFields traverse
        # related managers and need a SAVED tenant, and saving one with
        # ``schema_name="public"`` would have django-tenants create a
        # schema — far too much machinery to prove one CharField is
        # bound to the right attribute.
        tenant = Tenant(
            schema_name="public",
            name="t",
            slug="t",
            owner_email="t@example.com",
            openai_pixel_id="8MktrqpXN1MRdD2NUfkXmU",
        )

        field = TenantConfigSerializer().fields["openai_pixel_id"]

        self.assertEqual(field.get_attribute(tenant), "8MktrqpXN1MRdD2NUfkXmU")
