"""The anonymiser's value generation, redaction and scanning, without a DB."""

from __future__ import annotations

import phonenumbers

from devtools.anonymise import fakes, redaction, scan
from devtools.anonymise.engine import Subject
from devtools.anonymise.rules import Kind


def _subject(key: str = "user:7") -> Subject:
    return Subject(fakes.person(key), fakes.place("order.Order:9"))


def test_a_person_is_a_pure_function_of_its_key():
    assert fakes.person("user:1") == fakes.person("user:1")
    assert fakes.person("user:1") != fakes.person("user:2")


def test_emails_are_unique_per_key_and_undeliverable():
    emails = {fakes.person(f"user:{i}").email for i in range(2000)}

    assert len(emails) == 2000
    assert all(e.endswith("@staging.invalid") for e in emails)


def test_fake_phones_parse_as_valid_greek_mobiles():
    for i in range(50):
        parsed = phonenumbers.parse(fakes.person(f"user:{i}").phone)
        assert phonenumbers.is_valid_number(parsed)


def test_fake_vat_numbers_carry_a_valid_check_digit():
    for i in range(50):
        digits = [int(c) for c in fakes.place(f"p:{i}").vat_id]
        total = sum(d * 2 ** (8 - n) for n, d in enumerate(digits[:8]))
        assert len(digits) == 9
        assert digits[8] == total % 11 % 10


def test_json_keys_that_name_a_person_are_replaced_whatever_the_shape():
    subject = _subject()
    payload = {
        "Voucher_No": "7123456789",
        "create_request": {
            "Recipient_Name": "Real Person",
            "Recipient_Phone": "+306912345678",
            "Recipient_Address": "Ermou 5",
            "Recipient_Email": "real@example.gr",
            "Station_Origin": "TH",
            "items": [{"first_name": "Real", "carrier_name": "ACS"}],
        },
        "attempts": 2,
    }

    out = redaction.redact_json(payload, subject)

    request = out["create_request"]
    assert out["Voucher_No"] == "7123456789"
    assert out["attempts"] == 2
    assert request["Station_Origin"] == "TH"
    assert request["Recipient_Name"] == subject.person.full_name
    assert request["Recipient_Phone"] == subject.person.phone
    assert request["Recipient_Email"] == subject.person.email
    assert "Real" not in str(out) or "carrier_name" in str(out)
    assert request["items"][0]["first_name"] == subject.person.first_name
    assert request["items"][0]["carrier_name"] == "ACS"


def test_known_values_and_addresses_are_replaced_inside_any_string():
    subject = _subject()
    known = [("Real Person", subject.person.full_name)]

    out = redaction.redact_json(
        {"error": "bad request for real person <who@example.gr>"},
        subject,
        known,
    )

    assert "real person" not in out["error"].lower()
    assert "who@example.gr" not in out["error"]


def test_text_redaction_rewrites_phones_but_not_dates():
    subject = _subject()

    out = redaction.redact_text(
        "call +30 691 234 5678 or 6912345678 on 2026-10-17 12:30:00",
        subject,
    )

    assert "5678" not in out
    assert "2026-10-17 12:30:00" in out


def test_key_kinds():
    assert redaction.key_kind("Recipient_Name") is Kind.FULL_NAME
    assert redaction.key_kind("email") is Kind.EMAIL
    assert redaction.key_kind("ip") is Kind.IP
    assert redaction.key_kind("address_line_1") is Kind.STREET_LINE
    assert redaction.key_kind("file_name") is None
    assert redaction.key_kind("voucher_no") is None


def test_the_scan_finds_a_surviving_original_in_any_shape():
    canaries = scan.Canaries()
    canaries.add_email("Real@Example.gr")
    canaries.add_phone("+30 691 234 5678")
    canaries.add_ip("203.0.113.9")
    canaries.add_name("Μαρία", "Παπαδοπούλου")

    text = (
        '{"to": "REAL@example.gr", "tel": "6912345678", "ip": "203.0.113.9",'
        ' "who": "ΠΑΠΑΔΟΠΟΥΛΟΥ ΜΑΡΙΑ"}'
    )

    assert set(scan._hits(text, canaries)) == {"email", "phone", "ip", "name"}
    assert not scan._hits("nothing personal here", canaries)


def test_a_discarded_canary_is_not_a_leak():
    canaries = scan.Canaries()
    canaries.add_email("kept@example.gr")
    canaries.add_name("Μαρία", "Παπαδοπούλου")

    canaries.discard({"kept@example.gr", "Μαρία Παπαδοπούλου"})

    assert not canaries


def test_a_three_word_name_is_found_by_its_adjacent_pair():
    canaries = scan.Canaries()
    canaries.add_name("Μαρία Άννα", "Παπαδοπούλου")

    assert scan._hits("Άννα Παπαδοπούλου", canaries) == {"name": 1}
    assert scan._hits("Παπαδοπούλου Μαρία", canaries) == {"name": 1}


def test_every_fake_fits_the_column_it_replaces():
    from django.apps import apps

    from devtools.anonymise import engine, rules

    too_long = []
    for label, rule in rules.REGISTRY.items():
        if not isinstance(rule, rules.Scrub):
            continue
        model = apps.get_model(label)
        for name, spec in rule.fields.items():
            kind = spec.kind if isinstance(spec, rules.F) else spec
            limit = getattr(model._meta.get_field(name), "max_length", None)
            if kind in (Kind.TEXT, Kind.JSON, Kind.UUID) or not limit:
                continue
            original = (
                "7a1f0c9e-4c1d-4b7a-9d0e-1f2a3b4c5d6e"
                if kind is Kind.UUID
                else "attachment.pdf"
            )
            longest = max(
                len(
                    str(
                        engine._fake(
                            kind,
                            original,
                            f"{label}:{i}",
                            _subject(f"user:{i}"),
                            name,
                        )
                    )
                )
                for i in range(300)
            )
            if longest > limit:
                too_long.append(f"{label}.{name}: {longest} > {limit}")

    assert not too_long, too_long


def test_bearer_twins_differ_from_production_values_and_are_stable():
    import uuid

    original = uuid.uuid4()
    twin = fakes.remap_uuid(original)

    assert twin != original
    assert twin.version == 5
    assert fakes.remap_uuid(str(original)) == twin
    assert fakes.remap_uuid(twin) == twin
    code = fakes.remap_code("abcd1234efgh5678")
    assert code.startswith("STG-")
    assert fakes.remap_code("ABCD1234EFGH5678") == code
    assert fakes.remap_code(code) == code


def test_bearer_keys_in_json_are_replaced_with_the_rows_twin():
    import uuid

    cart = str(uuid.uuid4())
    out = redaction.redact_json(
        {"cart_snapshot": {"cart_uuid": cart}, "gift_card_code": "SECRET1"},
        _subject(),
    )

    assert out["cart_snapshot"]["cart_uuid"] == str(fakes.remap_uuid(cart))
    assert out["gift_card_code"] == fakes.remap_code("SECRET1")
