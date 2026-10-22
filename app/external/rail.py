from app.external.mcp_client import Session, env, iso_day

"""12306 火车票。余票和各席别票价。

地址放在 RAIL_MCP_URL，例如 http://127.0.0.1:8000/mcp。
先在本机用 HTTP 模式启动：uvx --from mcp-server-12306 mcp-12306
没配地址、日期对不上或请求失败时返回空列表。
"""

_URL_ENV = "RAIL_MCP_URL"
_TOOLS = ("query-tickets", "query-ticket-price", "query-transfer")
# 一个城市对一个城市，一天常有三四十趟。全留下，让调用方按时间和车型挑。
_MAX_TRAINS = 60
_SEAT_LABELS = (
    ("business", "商务座"),
    ("first_class", "一等座"),
    ("second_class", "二等座"),
    ("soft_sleeper", "软卧"),
    ("hard_sleeper", "硬卧"),
    ("hard_seat", "硬座"),
    ("no_seat", "无座"),
)


async def query_trains(origin: str, destination: str, day: str) -> list[dict]:
    """查这一天从出发站到到达站的车次、时刻、余票和票价。最多 15 趟。"""
    origin = (origin or "").strip()
    destination = (destination or "").strip()
    train_date = iso_day(day)
    url = env(_URL_ENV)
    if not origin or not destination or not train_date or not url:
        return []
    arguments = {
        "from_station": origin,
        "to_station": destination,
        "train_date": train_date,
    }
    tickets = None
    prices = None
    try:
        async with Session(url, _TOOLS) as client:
            try:
                tickets = await client.call("query-tickets", arguments)
            except Exception:
                tickets = None
            try:
                prices = await client.call("query-ticket-price", arguments)
            except Exception:
                prices = None
    except Exception:
        return []
    note = _api_note(tickets) or _api_note(prices)
    fare_by_code = _fares(prices)
    trains = []
    for row in _train_rows(tickets):
        public = str(row.get("train_code") or "")
        internal = str(row.get("train_no") or "")
        fare = fare_by_code.get(public) or fare_by_code.get(internal) or {}
        trains.append({
            "train_code": public or internal,
            "from_station": row.get("from_station") or origin,
            "to_station": row.get("to_station") or destination,
            "start_time": row.get("start_time") or "",
            "arrive_time": row.get("arrive_time") or "",
            "duration": row.get("duration") or "",
            "seats": _seat_text(row.get("seats")),
            "prices": fare,
        })
        if len(trains) >= _MAX_TRAINS:
            break
    if trains:
        return trains
    for code, fare in list(fare_by_code.items())[:_MAX_TRAINS]:
        trains.append({
            "train_code": code,
            "from_station": origin,
            "to_station": destination,
            "start_time": "",
            "arrive_time": "",
            "duration": "",
            "seats": "",
            "prices": fare,
        })
    if trains:
        return trains
    if note:
        return [{"note": note}]
    return trains


async def query_transfer(origin: str, destination: str, day: str, middle: str = "") -> list[dict]:
    """没有直达或直达太少时，查一次中转。按总历时从短到长，最多 8 套。

    每套是 {"middle", "wait", "total", "segments": [{train_code, from_station, ...}]}。
    """
    origin = (origin or "").strip()
    destination = (destination or "").strip()
    train_date = iso_day(day)
    url = env(_URL_ENV)
    if not origin or not destination or not train_date or not url:
        return []
    arguments = {"from_station": origin, "to_station": destination, "train_date": train_date}
    if (middle or "").strip():
        arguments["middle_station"] = middle.strip()
    try:
        async with Session(url, _TOOLS) as client:
            data = await client.call("query-transfer", arguments)
    except Exception:
        return []
    note = _api_note(data)
    if note:
        return [{"note": note}]
    found = data.get("transfers") if isinstance(data, dict) else None
    plans = []
    for row in found if isinstance(found, list) else []:
        if not isinstance(row, dict):
            continue
        segments = []
        for part in row.get("segments") or []:
            if not isinstance(part, dict):
                continue
            segments.append({
                "train_code": part.get("train_code") or "",
                "from_station": part.get("from_station") or "",
                "to_station": part.get("to_station") or "",
                "start_time": part.get("start_time") or "",
                "arrive_time": part.get("arrive_time") or "",
                "seats": _seat_text(part.get("seats")),
            })
        if segments:
            plans.append({
                "middle": row.get("middle_station") or "",
                "wait": row.get("wait_time") or "",
                "total": row.get("total_duration") or "",
                "segments": segments,
            })
    plans.sort(key=lambda plan: _minutes(plan["total"]))
    return plans[:8]


def _minutes(text: str) -> int:
    """「3小时27分钟」换成分钟，排序用。读不出就排到最后。"""
    text = str(text or "")
    hours = minutes = 0
    if "小时" in text:
        head, text = text.split("小时", 1)
        hours = int(head) if head.strip().isdigit() else 0
    if "分钟" in text:
        head = text.split("分钟", 1)[0]
        minutes = int(head) if head.strip().isdigit() else 0
    total = hours * 60 + minutes
    return total if total else 10 ** 6


def _api_note(data) -> str:
    """12306 拒绝这次查询时，把原因留下。例如出发日超过提前 14 天。"""
    if not isinstance(data, dict) or data.get("success") is not False:
        return ""
    errors = data.get("errors")
    if isinstance(errors, list):
        text = "；".join(str(item).strip() for item in errors if str(item).strip())
        if text:
            return text
    if isinstance(errors, str) and errors.strip():
        return errors.strip()
    message = data.get("message") or data.get("error") or ""
    return str(message).strip()


def _train_rows(data) -> list[dict]:
    if not isinstance(data, dict):
        return []
    rows = data.get("trains")
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, dict)]
    return []


def _fares(data) -> dict[str, dict]:
    if not isinstance(data, dict):
        return {}
    rows = data.get("data")
    found = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        prices = row.get("prices")
        if not isinstance(prices, dict):
            continue
        fare = {str(name): value for name, value in prices.items() if value not in (None, "")}
        for key in ("train_code", "train_no"):
            code = str(row.get(key) or "")
            if code:
                found[code] = fare
    return found


def _seat_text(seats) -> str:
    if not isinstance(seats, dict):
        return ""
    parts = []
    used = set()
    for key, label in _SEAT_LABELS:
        value = seats.get(key)
        if value not in (None, ""):
            parts.append(f"{label}{value}")
            used.add(key)
    for key, value in seats.items():
        if key in used or value in (None, ""):
            continue
        parts.append(f"{key}{value}")
    return " ".join(parts)
