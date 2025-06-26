from datetime import date, datetime, timedelta


def date_range(
    start_date: date | datetime,
    end_date: date | datetime,
    step: int = 1,
) -> list[date]:
    if isinstance(start_date, datetime):
        start_date = start_date.date()
    if isinstance(end_date, datetime):
        end_date = end_date.date()

    result = []
    current_date = start_date
    while current_date <= end_date:
        result.append(current_date)
        current_date += timedelta(days=step)

    return result
