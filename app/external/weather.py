import json
import re
from datetime import date, timedelta
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

"""天气查询。国内用中国天气网，海外用 Open-Meteo。

国内：7 天页、15 天页，更远再读 40 天日历。海外最长 16 天。
"""

_GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_CITY_SEARCH = "http://toy1.weather.com.cn/search"
_PAGE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.weather.com.cn/",
}

# 世界气象组织天气码，收成写稿用的中文。含「雨」「雪」的字，后面会用来决定要不要搜室内。
_WEATHER_TEXT = {
    0: "晴",
    1: "大部晴",
    2: "多云",
    3: "阴",
    45: "雾",
    48: "雾凇",
    51: "小毛毛雨",
    53: "毛毛雨",
    55: "大毛毛雨",
    56: "冻毛毛雨",
    57: "强冻毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "冻雨",
    67: "强冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "雪粒",
    80: "小阵雨",
    81: "阵雨",
    82: "强阵雨",
    85: "小阵雪",
    86: "阵雪",
    95: "雷暴",
    96: "雷暴伴小冰雹",
    99: "雷暴伴大冰雹",
}


async def forecast(area: str, day: str, days: int = 1) -> str:
    """按地名和日历日期查天气。

    中国城市走中国天气网。找不到城市代码就当海外，走 Open-Meteo。
    day 没写年份时是「12月5日」，写了年份时是 2026-12-05。
    对不上的日子不写。
    """
    area = (area or "").strip()
    dates = _calendar_days(_wanted_days(day, days))
    if not area or not dates:
        return ""
    try:
        async with httpx.AsyncClient(timeout=12, headers=_PAGE_HEADERS, follow_redirects=True) as client:
            code = await _china_code(client, area)
            if code:
                found, city = await _china_lines(client, code, area, dates)
            else:
                found = await _open_lines(client, area, dates)
                city = area
    except Exception:
        return ""
    lines = []
    for current in dates:
        line = found.get(current)
        if isinstance(line, tuple):
            city, text = line
            lines.append(text)
        elif line:
            lines.append(line)
    if not lines:
        return ""
    return f"{city}天气预报（只使用下面这些，不要另编）：\n" + "\n".join(lines)


def _calendar_days(wanted: list[tuple[int | None, int, int]]) -> list[date]:
    """把月日收成今天起 40 天里的具体日期。已经过去的不要。"""
    today = date.today()
    days = []
    for year, month, day_num in wanted:
        current = _resolve_day(year, month, day_num, today)
        if current is None or current < today or (current - today).days > 39:
            continue
        days.append(current)
    return days


def _resolve_day(year: int | None, month: int, day_num: int, today: date) -> date | None:
    """没写年份时，在今天起 40 天里找这个月日。"""
    if year is not None:
        return _make_date(year, month, day_num)
    for offset in range(40):
        current = today + timedelta(days=offset)
        if current.month == month and current.day == day_num:
            return current
    return None


async def _china_code(client: httpx.AsyncClient, area: str) -> str:
    """中国天气网的 9 位城市代码。景点、海外城市返回空。"""
    names = []
    for name in (area, area.removesuffix("市")):
        if name and name not in names:
            names.append(name)
    for name in names:
        response = await client.get(_CITY_SEARCH, params={"cityname": name})
        response.raise_for_status()
        text = response.text
        start, end = text.find("["), text.rfind("]")
        if start < 0 or end <= start:
            continue
        try:
            rows = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            continue
        for row in rows if isinstance(rows, list) else []:
            parts = str((row or {}).get("ref") or "").split("~")
            # 国内城市代码以 101 开头。海外和景点代码不走这里。
            if len(parts) < 3 or not parts[0].startswith("101") or len(parts[0]) != 9:
                continue
            if parts[2] in names:
                return parts[0]
    return ""


async def _china_lines(
    client: httpx.AsyncClient, code: str, area: str, days: list[date],
) -> tuple[dict[date, str], str]:
    """7 天、15 天页面，更远的日期再读 40 天日历。"""
    today = date.today()
    found: dict[date, str] = {}
    if any((current - today).days <= 6 for current in days):
        html = (await client.get(f"https://www.weather.com.cn/weather/{code}.shtml")).text
        found.update(_parse_7d(html, today))
    if any(7 <= (current - today).days <= 14 for current in days):
        html = (await client.get(f"https://www.weather.com.cn/weather15d/{code}.shtml")).text
        found.update(_parse_15d(html, today))
    late = [current for current in days if (current - today).days >= 15]
    for year, month in {(current.year, current.month) for current in late}:
        url = (
            f"https://d1.weather.com.cn/calendar_new/{year}/"
            f"{code}_{year}{month:02d}.html"
        )
        text = (await client.get(url)).text
        found.update(_parse_40d(text))
    return {current: found[current] for current in days if current in found}, area


def _parse_7d(html: str, today: date) -> dict[date, str]:
    """7 天页里每一天的日期、天气、气温和风力。"""
    start = html.find('id="7d"')
    if start < 0:
        return {}
    block = html[start:start + 30000]
    found = {}
    cursor = today
    for item in re.findall(r"<li[^>]*>(.*?)</li>", block, re.S):
        day = re.search(r"<h1>(\d+)日", item)
        weather = re.search(r'class="wea">([^<]*)', item)
        temps = re.search(r"<span>(\d+)</span>/<i>(\d+)", item)
        power = re.search(r'class="win".*?<i>(.*?)</i>', item, re.S)
        if not day or not weather or not temps:
            continue
        cursor = _advance_to(cursor, int(day.group(1)))
        if cursor is None:
            break
        wind = (power.group(1).strip() if power else "")
        found[cursor] = _format_line(cursor, weather.group(1), temps.group(2), temps.group(1), wind)
        cursor = cursor + timedelta(days=1)
    return found


def _parse_15d(html: str, today: date) -> dict[date, str]:
    """15 天页后半段。从今天往后对上日号。"""
    start = html.find('id="15d"')
    if start < 0:
        return {}
    block = html[start:start + 20000]
    found = {}
    cursor = today
    for item in re.findall(r"<li[^>]*>(.*?)</li>", block, re.S):
        day = re.search(r"（(\d+)日）", item)
        weather = re.search(r'class="wea">([^<]*)', item)
        temps = re.search(r"<em>(\d+)℃</em>/(\d+)", item)
        wind = re.search(r'class="wind">([^<]*)', item)
        power = re.search(r'class="wind1">(.*?)</span>', item, re.S)
        if not day or not weather or not temps:
            continue
        cursor = _advance_to(cursor, int(day.group(1)))
        if cursor is None:
            break
        breeze = " ".join(part.strip() for part in (
            wind.group(1) if wind else "",
            power.group(1) if power else "",
        ) if part and part.strip())
        found[cursor] = _format_line(cursor, weather.group(1), temps.group(2), temps.group(1), breeze)
        cursor = cursor + timedelta(days=1)
    return found


def _parse_40d(text: str) -> dict[date, str]:
    """40 天日历脚本。日期、天气、最高最低温、风向。"""
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return {}
    try:
        rows = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {}
    found = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        raw = str(row.get("date") or "")
        if len(raw) != 8 or not raw.isdigit():
            continue
        current = _make_date(int(raw[:4]), int(raw[4:6]), int(raw[6:]))
        weather = str(row.get("w1") or "").strip()
        if current is None or not weather:
            continue
        wind = str(row.get("wd1") or "").strip()
        found[current] = _format_line(current, weather, row.get("min"), row.get("max"), wind)
    return found


def _advance_to(cursor: date, day_num: int) -> date | None:
    """从当前日期往前找到这个日号。跨月就继续加。"""
    for _ in range(40):
        if cursor.day == day_num:
            return cursor
        cursor = cursor + timedelta(days=1)
    return None


def _format_line(current: date, weather: str, low, high, wind: str) -> str:
    label = f"{current.month}月{current.day}日"
    low_text = str(low or "").replace("℃", "")
    high_text = str(high or "").replace("℃", "")
    temp = f"{low_text}到{high_text}℃" if low_text and high_text else ""
    wind = (wind or "").replace("<", "低于").strip()
    return " ".join(part for part in (label, (weather or "").strip(), temp, wind) if part)


async def _open_lines(client: httpx.AsyncClient, area: str, days: list[date]) -> dict[date, str]:
    """海外城市的 Open-Meteo 预报。只留下要对的那几天。"""
    place = await _locate(client, area)
    if place is None:
        return {}
    latitude, longitude, _city = place
    response = await client.get(
        _FORECAST_URL,
        params={
            "latitude": latitude,
            "longitude": longitude,
            "timezone": "auto",
            "forecast_days": 16,
            "daily": ",".join((
                "weather_code",
                "temperature_2m_max",
                "temperature_2m_min",
                "wind_direction_10m_dominant",
                "wind_speed_10m_max",
            )),
        },
    )
    response.raise_for_status()
    data = response.json()
    daily = data.get("daily") if isinstance(data, dict) else None
    if not isinstance(daily, dict):
        return {}
    found = {}
    for current in days:
        if (current - date.today()).days > 15:
            continue
        line = _open_day_line(daily, current)
        if line:
            found[current] = line
    return found


async def _locate(client: httpx.AsyncClient, area: str) -> tuple[float, float, str] | None:
    """把城市名换成坐标。武汉、武汉市都试。找不到返回空。"""
    names = []
    for name in (area, area.removesuffix("市"), area.removesuffix("省")):
        if name and name not in names:
            names.append(name)
    for name in names:
        response = await client.get(
            _GEO_URL,
            params={
                "name": name,
                "count": 1,
                "language": "zh",
            },
        )
        response.raise_for_status()
        data = response.json()
        results = data.get("results") if isinstance(data, dict) else None
        if not results or not isinstance(results[0], dict):
            continue
        place = results[0]
        latitude = place.get("latitude")
        longitude = place.get("longitude")
        if not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):
            continue
        city = place.get("name") or area
        return float(latitude), float(longitude), str(city)
    return None


def _open_day_line(daily: dict, current: date) -> str:
    """对上的那一天写成：日期、天气、气温、风向风力。对不上返回空。"""
    times = daily.get("time") or []
    index = None
    for position, raw in enumerate(times):
        pieces = str(raw).split("-")
        if len(pieces) != 3 or not all(piece.isdigit() for piece in pieces):
            continue
        cast_year, cast_month, cast_day = (int(piece) for piece in pieces)
        if cast_year == current.year and cast_month == current.month and cast_day == current.day:
            index = position
            break
    if index is None:
        return ""
    code = _at(daily, "weather_code", index)
    low = _at(daily, "temperature_2m_min", index)
    high = _at(daily, "temperature_2m_max", index)
    direction = _at(daily, "wind_direction_10m_dominant", index)
    speed = _at(daily, "wind_speed_10m_max", index)
    label = f"{current.month}月{current.day}日"
    weather = _WEATHER_TEXT.get(int(code), "阴") if code is not None else ""
    temp = ""
    if low is not None and high is not None:
        temp = f"{round(float(low))}到{round(float(high))}℃"
    wind_text = _wind_text(direction, speed)
    return " ".join(part for part in (label, weather, temp, wind_text) if part)


def _at(daily: dict, key: str, index: int):
    values = daily.get(key) or []
    if index >= len(values):
        return None
    value = values[index]
    if value is None:
        return None
    return value


def _wind_text(direction, speed) -> str:
    """风向用 16 方位里的 8 个汉字。风速按公里每小时收成风力等级。"""
    if not isinstance(direction, (int, float)) or not isinstance(speed, (int, float)):
        return ""
    names = ("北", "东北", "东", "东南", "南", "西南", "西", "西北")
    slot = int((float(direction) + 22.5) % 360 // 45)
    limits = (1, 6, 12, 20, 29, 39, 50, 62, 75, 89, 103, 118)
    level = 0
    for limit in limits:
        if float(speed) >= limit:
            level += 1
    return f"{names[slot]}风 {level}级"


def _wanted_days(day: str, count: int) -> list[tuple[int | None, int, int]]:
    """从出发日期起列出要查的月日。有年份就带上年份。最多 40 天。"""
    parts = _date_parts(day)
    if parts is None:
        return []
    year, month, day_num = parts
    count = count if count > 0 else 1
    if count > 40:
        count = 40
    if year is not None:
        start = _make_date(year, month, day_num)
        if start is None:
            return []
        days = []
        for offset in range(count):
            current = start + timedelta(days=offset)
            days.append((current.year, current.month, current.day))
        return days
    days = []
    for offset in range(count):
        shifted = _shift_month_day(month, day_num, offset)
        if shifted is None:
            break
        days.append((None, shifted[0], shifted[1]))
    return days


def _shift_month_day(month: int, day: int, offset: int) -> tuple[int, int] | None:
    """只把月日往后推几天，不决定是哪一年。"""
    if not 1 <= month <= 12:
        return None
    lengths = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    index = month - 1
    day += offset
    while day > lengths[index]:
        day -= lengths[index]
        index = (index + 1) % 12
    while day < 1:
        index = (index - 1) % 12
        day += lengths[index]
    return index + 1, day


def _date_parts(value: str) -> tuple[int | None, int, int] | None:
    """只拆模型已经收好的两种模板：10月29日，或 2026-10-29。"""
    text = (value or "").strip()
    if text.endswith("日") and "月" in text:
        month_text, day_text = text[:-1].split("月", 1)
        if month_text.isdigit() and day_text.isdigit():
            return None, int(month_text), int(day_text)
        return None
    pieces = text.split("-")
    if len(pieces) == 3 and all(piece.isdigit() for piece in pieces):
        return int(pieces[0]), int(pieces[1]), int(pieces[2])
    return None


def _make_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None

