from app.external.mcp_client import Session, env, iso_day, rows

"""飞常准机票。两城之间当天在售航班的逐舱票价。

密钥放在 VARIFLIGHT_API_KEY。城市用中文名或 IATA 城市码，例如北京或 BJS。
没配密钥、城市对不上码、日期对不上或请求失败时返回空列表。按次计费。
"""

_URL = "https://ai.variflight.com/servers/tripmatch/mcp"
_TOOLS = ("getFlightPriceByCities",)
_CITY_CODE = {
    "北京": "BJS",
    "上海": "SHA",
    "广州": "CAN",
    "深圳": "SZX",
    "成都": "CTU",
    "杭州": "HGH",
    "西安": "XIY",
    "南京": "NKG",
    "重庆": "CKG",
    "武汉": "WUH",
    "厦门": "XMN",
    "青岛": "TAO",
    "昆明": "KMG",
    "三亚": "SYX",
    "大连": "DLC",
    "天津": "TSN",
    "长沙": "CSX",
    "郑州": "CGO",
    "海口": "HAK",
    "沈阳": "SHE",
    "哈尔滨": "HRB",
    "福州": "FOC",
    "济南": "TNA",
    "贵阳": "KWE",
    "南宁": "NNG",
    "合肥": "HFE",
    "南昌": "KHN",
    "兰州": "LHW",
    "乌鲁木齐": "URC",
    "拉萨": "LXA",
    "呼和浩特": "HET",
    "银川": "INC",
    "西宁": "XNN",
    "太原": "TYN",
    "石家庄": "SJW",
    "长春": "CGQ",
    "桂林": "KWL",
    "丽江": "LJG",
    "大理": "DLU",
    "珠海": "ZUH",
    "宁波": "NGB",
    "温州": "WNZ",
    "无锡": "WUX",
    "烟台": "YNT",
}


async def search_flights(origin: str, destination: str, day: str) -> list[dict]:
    """查出发城市到到达城市、这一天的航班票价。最多 15 班。"""
    dep = _city_code(origin)
    arr = _city_code(destination)
    dep_date = iso_day(day)
    api_key = env("VARIFLIGHT_API_KEY")
    if not dep or not arr or not dep_date or not api_key:
        return []
    try:
        async with Session(_URL, _TOOLS, {"X-API-Key": api_key}) as client:
            data = await client.call("getFlightPriceByCities", {
                "dep_city": dep,
                "arr_city": arr,
                "dep_date": dep_date,
            })
    except Exception:
        return []
    flights = []
    for row in rows(data, "flights", "data", "list", "items"):
        flights.append({
            "flight_no": _first(row, "fnum", "flight_no", "flightNo", "flight_number"),
            "airline": _first(row, "airline", "airline_name", "airway"),
            "depart": _first(row, "dep_time", "departureTime", "depTime", "plan_dep_time"),
            "arrive": _first(row, "arr_time", "arrivalTime", "arrTime", "plan_arr_time"),
            "prices": _prices(row),
        })
        if len(flights) >= 15:
            break
    return [item for item in flights if item["flight_no"] or item["prices"]]


def _city_code(name: str) -> str:
    text = (name or "").strip()
    if len(text) == 3 and text.isascii() and text.isalpha():
        return text.upper()
    for suffix in ("市", "省"):
        if text.endswith(suffix) and len(text) > len(suffix):
            text = text[:-len(suffix)]
            break
    return _CITY_CODE.get(text, "")


def _first(row: dict, *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _prices(row: dict) -> dict:
    found = row.get("prices") or row.get("cabins") or row.get("fares")
    if isinstance(found, dict):
        return {str(name): value for name, value in found.items() if value not in (None, "")}
    if isinstance(found, list):
        cabins = {}
        for item in found:
            if not isinstance(item, dict):
                continue
            name = _first(item, "cabin", "cabin_name", "seat_class", "name")
            price = item.get("price")
            if price in (None, ""):
                price = item.get("amount")
            if name and price not in (None, ""):
                cabins[name] = price
        return cabins
    price = row.get("price")
    if price not in (None, ""):
        return {"票价": price}
    return {}
