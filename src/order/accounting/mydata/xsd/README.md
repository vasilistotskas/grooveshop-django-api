# myDATA XSD schemas

AADE's public myDATA **v2.0.2** schemas (September 2026), as published at
https://www.aade.gr/mydata/tehnikes-prodiagrafes-ekdoseis-mydata
(`v2.0.2XSDs.zip`), so that
`order.accounting.mydata.validator.validate_invoice_doc` checks every
`InvoicesDoc` before it is sent:

- `InvoicesDoc-v2.0.2.xsd` — the document (`INVOICE_SCHEMA_FILENAME`);
- `incomeClassification-v2.0.2.xsd`, `expensesClassification-v2.0.2.xsd` —
  the namespaces it imports;
- `TransportTypes-v2.0.2.xsd`, `SimpleTypes-v2.0.2.xsd` — the types it
  includes.

The files are AADE's, unchanged. The rest of the zip (the response, the
request and the delivery-note methods' schemas) is not used here.

## A new AADE version

1. Replace these files with the new version's, under their new names.
2. Update `INVOICE_SCHEMA_FILENAME` in `order.accounting.mydata.validator`.
3. Run `tests/unit/order/mydata/`: `test_payloads_match_the_schema.py`
   builds every document the platform sends and validates it.
