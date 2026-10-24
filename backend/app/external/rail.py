import re

import httpx

from app.external.mcp_client import Session, env, iso_day

"""12306 火车票。余票、各席别原价和折后价。

地址放在 RAIL_MCP_URL，例如 http://127.0.0.1:8000/mcp。
先在本机用 HTTP 模式启动：uvx --from mcp-server-12306 mcp-12306
没配地址、日期对不上或请求失败时返回空列表。

12306 现在按车次和席别浮动打折。MCP 只给原价，折扣要另外从官网余票接口取。
取不到折扣时照常返回原价，折扣写空，不挡住查票。
"""

_URL_ENV = "RAIL_MCP_URL"
# 连不上本机服务时交回去的原话。和「没开售」「没有车」分开，免得代理猜原因。
_DOWN = "12306 服务没连上，不是没开售，也不是没有车。请先在本机启动 12306 服务"
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
# 官网折扣串里，每组第一个字符是席别。和上面的中文席别名对上。
_DISCOUNT_SEATS = {
    "9": "商务座",
    "P": "特等座",
    "M": "一等座",
    "O": "二等座",
    "W": "无座",
    "1": "硬座",
    "3": "硬卧",
    "4": "软卧",
}
_LEFT_TICKET_INIT = "https://kyfw.12306.cn/otn/leftTicket/init"
_LEFT_TICKET_URLS = (
    "https://kyfw.12306.cn/otn/leftTicket/queryG",
    "https://kyfw.12306.cn/otn/leftTicket/query",
)


async def query_trains(origin: str, destination: str, day: str) -> list[dict]:
    """查这一天从出发站到到达站的车次、时刻、余票和票价。

    每趟的 fares 是全部席别：席别、余票、原价、折扣、折后价。二等座卖完了，一等座和商务座也会在里面。
    """
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
        return [{"note": _DOWN}]
    note = _api_note(tickets) or _api_note(prices)
    fare_by_code = _fares(prices)
    rows = _train_rows(tickets)
    discounts = await _discounts(rows, train_date)
    trains = []
    for row in rows:
        public = str(row.get("train_code") or "")
        internal = str(row.get("train_no") or "")
        code = public or internal
        fare = fare_by_code.get(public) or fare_by_code.get(internal) or {}
        trains.append({
            "train_code": code,
            "from_station": row.get("from_station") or origin,
            "to_station": row.get("to_station") or destination,
            "start_time": row.get("start_time") or "",
            "arrive_time": row.get("arrive_time") or "",
            "duration": row.get("duration") or "",
            "seats": _seat_text(row.get("seats")),
            "prices": fare,
            "fares": _seat_fares(row.get("seats"), fare, discounts.get(code) or {}),
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
            "fares": _seat_fares({}, fare, {}),
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
        return [{"note": _DOWN}]
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


async def _discounts(rows: list[dict], train_date: str) -> dict[str, dict[str, int]]:
    """从 12306 官网余票接口取每趟车各席别的折扣，例如 {"G584": {"二等座": 93}}。93 就是 93 折。

    用 MCP 返回的车站代码去查。官网会把同城其他车站的车次一起带回。
    失败就返回空字典，调用方只显示原价。
    """
    first = next((row for row in rows if row.get("from_station_code") and row.get("to_station_code")), None)
    if not first:
        return {}
    params = {
        "leftTicketDTO.train_date": train_date,
        "leftTicketDTO.from_station": first["from_station_code"],
        "leftTicketDTO.to_station": first["to_station_code"],
        "purpose_codes": "ADULT",
    }
    try:
        async with httpx.AsyncClient(
            timeout=15,
            trust_env=False,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0"},
        ) as client:
            # 先打开一次查询页拿到 cookie，不然余票接口会跳回首页
            await client.get(_LEFT_TICKET_INIT)
            for url in _LEFT_TICKET_URLS:
                response = await client.get(url, params=params)
                try:
                    result = response.json()["data"]["result"]
                except Exception:
                    continue
                return _parse_discounts(result)
    except Exception:
        return {}
    return {}


def _parse_discounts(result) -> dict[str, dict[str, int]]:
    """官网每趟车是一长串用竖线隔开的字段。第 4 段是车次，靠后有一段形如 90086M0093O0093 的折扣串。"""
    found = {}
    for line in result if isinstance(result, list) else []:
        parts = str(line).split("|")
        if len(parts) < 10:
            continue
        code = parts[3]
        text = next(
            (part for part in reversed(parts) if part and re.fullmatch(r"(?:[0-9A-Z]\d{4})+", part)),
            "",
        )
        seats = {}
        for seat, rate in re.findall(r"([0-9A-Z])(\d{4})", text):
            name = _DISCOUNT_SEATS.get(seat)
            value = int(rate)
            if name and 0 < value < 100:
                seats[name] = value
        if code and seats:
            found[code] = seats
    return found


def _seat_fares(seats, prices: dict, discounts: dict[str, int]) -> list[dict]:
    """把余票、原价和折扣并成一张表，每个席别一行。顺序按商务座、一等座、二等座往下排。

    余票是「有」「无」或剩几张。没有折扣时，折后价等于原价，discount 留 0。
    """
    seats = seats if isinstance(seats, dict) else {}
    prices = prices if isinstance(prices, dict) else {}
    known = {label for _, label in _SEAT_LABELS}
    # 价格表里有、但上面没列的席别（如动卧），排在最后，余票不知道就空着
    order = list(_SEAT_LABELS) + [("", name) for name in prices if name not in known]
    rows = []
    for key, label in order:
        left = seats.get(key) if key else None
        price = _number(prices.get(label))
        # 无座和二等座同价，12306 价格表里通常不单列
        if price is None and label == "无座":
            price = _number(prices.get("二等座"))
        if left in (None, "") and price is None:
            continue
        rate = discounts.get(label, 0)
        final = round(price * rate / 100) if price is not None and rate else price
        rows.append({
            "seat": label,
            "left": str(left) if left not in (None, "") else "",
            "price": price,
            "discount": rate,
            "final": final,
        })
    return rows


def _number(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
