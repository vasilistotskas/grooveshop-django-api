# myDATA XSD Schemas

Drop the official AADE `InvoicesDoc-v1.0.10.xsd` (and any
transitively-imported schema files) here to enable **local XSD
pre-validation** of outbound payloads. When this directory is empty,
`order.mydata.validator.validate_invoice_doc` is a no-op — AADE
still validates server-side, so the integration is correct either
way; pre-validation just gives you faster feedback on master-data
drift.

## Where to get the XSDs

The official schemas are hosted behind auth on the AADE developer
portal (https://mydata-dev.azure-api.net/). After your subscription
is approved, download them from the portal and commit them to this
directory. File names must match the `INVOICE_SCHEMA_FILENAME`
constant in `order.mydata.validator`.

## Versioning

The integration is pinned to **v1.0.10** (AADE pre-official ERP
documentation, dated Nov 2024). Bumping versions is mechanical:

1. Drop the new XSDs here.
2. Update `INVOICE_SCHEMA_FILENAME` in `order.mydata.validator`.
3. Re-run the `tests/unit/order/mydata/` suite — failures pinpoint
   fields that changed shape.

## Do not commit copyrighted schemas

If your AADE subscription terms forbid redistributing the XSD files,
leave them out of the repo and populate them at deploy time via
ConfigMap / Secret. The integration works without them; you just
lose the local fail-fast check.
