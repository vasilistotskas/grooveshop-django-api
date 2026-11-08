import pytest
from rest_framework.exceptions import ValidationError

from core.api.query_params import MAX_INT_CSV_ITEMS, parse_int_csv


def _ids(count: int) -> str:
    return ",".join(str(i) for i in range(1, count + 1))


def test_empty_values_give_an_empty_list():
    assert parse_int_csv(None, "brands") == []
    assert parse_int_csv("", "brands") == []


def test_parses_and_strips_values():
    assert parse_int_csv(" 1, 2 ,,3 ", "brands") == [1, 2, 3]


def test_the_limit_itself_is_accepted():
    assert len(parse_int_csv(_ids(MAX_INT_CSV_ITEMS), "brands")) == (
        MAX_INT_CSV_ITEMS
    )


def test_over_the_limit_is_rejected_with_a_stable_code():
    with pytest.raises(ValidationError) as exc:
        parse_int_csv(_ids(MAX_INT_CSV_ITEMS + 1), "brands")
    detail = exc.value.detail["brands"]
    assert detail.code == "too_many_values"
    assert str(MAX_INT_CSV_ITEMS) in str(detail)


def test_the_count_is_checked_before_values_are_converted():
    # Garbage past the limit is reported as too many, proving no int() ran.
    with pytest.raises(ValidationError) as exc:
        garbage = [f"x{i}" for i in range(MAX_INT_CSV_ITEMS + 1)]
        parse_int_csv(",".join(garbage), "brands")
    assert exc.value.detail["brands"].code == "too_many_values"


def test_custom_limit_is_honoured():
    assert parse_int_csv("1,2", "ids", max_items=2) == [1, 2]
    with pytest.raises(ValidationError):
        parse_int_csv("1,2,3", "ids", max_items=2)


def test_garbage_is_still_invalid_integer_list():
    with pytest.raises(ValidationError) as exc:
        parse_int_csv("1,two,3", "categories")
    assert exc.value.detail["categories"].code == "invalid_integer_list"


def test_no_limit_when_the_caller_bounds_the_list_itself():
    ids = ",".join(str(i) for i in range(1, 202))

    assert parse_int_csv(ids, "productIds", max_items=None) == list(
        range(1, 202)
    )


def test_repeated_ids_count_once():
    repeated = ",".join(["7"] * (MAX_INT_CSV_ITEMS + 1))

    assert parse_int_csv(repeated, "brands") == [7]
