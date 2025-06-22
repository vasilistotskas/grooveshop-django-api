from datetime import UTC, date, datetime, timedelta

from core.utils.dates import date_range


class TestDateRange:
    def test_date_range_basic(self):
        start = date(2024, 1, 1)
        end = date(2024, 1, 5)

        result = date_range(start, end)

        expected = [
            date(2024, 1, 1),
            date(2024, 1, 2),
            date(2024, 1, 3),
            date(2024, 1, 4),
            date(2024, 1, 5),
        ]
        assert result == expected

    def test_date_range_with_datetime_objects(self):
        start = datetime(2024, 1, 1, 10, 30, 0, tzinfo=UTC)
        end = datetime(2024, 1, 3, 15, 45, 0, tzinfo=UTC)

        result = date_range(start, end)

        expected = [
            date(2024, 1, 1),
            date(2024, 1, 2),
            date(2024, 1, 3),
        ]
        assert result == expected

    def test_date_range_mixed_date_and_datetime(self):
        start = date(2024, 1, 1)
        end = datetime(2024, 1, 3, 12, 0, 0, tzinfo=UTC)

        result = date_range(start, end)

        expected = [
            date(2024, 1, 1),
            date(2024, 1, 2),
            date(2024, 1, 3),
        ]
        assert result == expected

    def test_date_range_with_step(self):
        start = date(2024, 1, 1)
        end = date(2024, 1, 10)

        result = date_range(start, end, step=2)

        expected = [
            date(2024, 1, 1),
            date(2024, 1, 3),
            date(2024, 1, 5),
            date(2024, 1, 7),
            date(2024, 1, 9),
        ]
        assert result == expected

    def test_date_range_with_large_step(self):
        start = date(2024, 1, 1)
        end = date(2024, 1, 31)

        result = date_range(start, end, step=7)

        expected = [
            date(2024, 1, 1),
            date(2024, 1, 8),
            date(2024, 1, 15),
            date(2024, 1, 22),
            date(2024, 1, 29),
        ]
        assert result == expected

    def test_date_range_single_day(self):
        start = date(2024, 1, 15)
        end = date(2024, 1, 15)

        result = date_range(start, end)

        expected = [date(2024, 1, 15)]
        assert result == expected

    def test_date_range_empty_when_start_after_end(self):
        start = date(2024, 1, 10)
        end = date(2024, 1, 5)

        result = date_range(start, end)

        assert result == []

    def test_date_range_across_months(self):
        start = date(2024, 1, 30)
        end = date(2024, 2, 2)

        result = date_range(start, end)

        expected = [
            date(2024, 1, 30),
            date(2024, 1, 31),
            date(2024, 2, 1),
            date(2024, 2, 2),
        ]
        assert result == expected

    def test_date_range_across_years(self):
        start = date(2023, 12, 30)
        end = date(2024, 1, 2)

        result = date_range(start, end)

        expected = [
            date(2023, 12, 30),
            date(2023, 12, 31),
            date(2024, 1, 1),
            date(2024, 1, 2),
        ]
        assert result == expected

    def test_date_range_leap_year(self):
        start = date(2024, 2, 28)
        end = date(2024, 3, 1)

        result = date_range(start, end)

        expected = [
            date(2024, 2, 28),
            date(2024, 2, 29),
            date(2024, 3, 1),
        ]
        assert result == expected

    def test_date_range_default_step_parameter(self):
        start = date(2024, 1, 1)
        end = date(2024, 1, 3)

        result_with_default = date_range(start, end)
        result_with_explicit = date_range(start, end, step=1)

        assert result_with_default == result_with_explicit

    def test_date_range_step_zero_or_negative(self):
        start = date(2024, 1, 1)
        end = date(2024, 1, 5)
        result = date_range(start, end, step=1)
        assert len(result) == 5

    def test_date_range_large_range(self):
        start = date(2024, 1, 1)
        end = date(2024, 1, 1) + timedelta(days=365)

        result = date_range(start, end, step=30)

        assert len(result) > 10
        assert len(result) < 15
        assert result[0] == start

    def test_date_range_return_type(self):
        start = date(2024, 1, 1)
        end = date(2024, 1, 3)

        result = date_range(start, end)

        assert isinstance(result, list)
        assert all(isinstance(d, date) for d in result)
        assert all(not isinstance(d, datetime) for d in result)

    def test_date_range_preserves_order(self):
        start = date(2024, 1, 1)
        end = date(2024, 1, 10)

        result = date_range(start, end, step=3)

        for i in range(1, len(result)):
            assert result[i] > result[i - 1]
