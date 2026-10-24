from datetime import date

"""用户只说月日、没说年份时补年份。"""


def next_month_day(month: int, day: int, today: date | None = None) -> date | None:
    """今年这一天还没过（含今天）就是今年，已经过了就是明年。

    今天 2026-10-24：说 11月5日是 2026-11-05，说 1月1日是 2027-01-01。
    2月29日往后找到最近的闰年。月日本身不存在就返回 None。
    """
    today = today or date.today()
    for year in range(today.year, today.year + 9):
        try:
            current = date(year, month, day)
        except ValueError:
            continue
        if current >= today:
            return current
    return None
