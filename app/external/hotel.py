from datetime import date

from app.external.mcp_client import Session, env, iso_day

"""酒店可订房价。优先用 RollingGo（中文店名、每晚展示价），没有密钥或没结果时用 Pilot。

RollingGo 国内地址是 https://mcp.rollinggo.cn/mcp，密钥放 ROLLINGGO_API_KEY。
只查 searchHotels，不下单。返回的 lowestPrice 是每晚展示价，不是锁定价。
Pilot 地址是 https://mcp.pilotplans.com/mcp，不用密钥。它的 price 是这段入住的总价，店名多半是英文。
near 要写中文地名，例如「河坊街」。没写日期或请求失败时返回空列表。
"""

_URL = "https://mcp.pilotplans.com/mcp"
_TOOLS = ("search_hotels",)
_ROLLING_URL = "https://mcp.rollinggo.cn/mcp"
_ROLLING_TOOLS = ("searchHotels",)


async def search_hotels(
    city: str,
    check_in: str = "",
    check_out: str = "",
    near: str = "",
    max_price: int = 0,
    guests: int = 2,
    rooms: int = 1,
    radius: int = 3000,
) -> list[dict]:
    """查这座城市在这几天能订的酒店，最多 10 家。

    max_price 是每晚上限。超过的不删，标上 over_budget，让调用方自己决定。
    """
    city = (city or "").strip()
    near = (near or "").strip()
    check_in = iso_day(check_in)
    check_out = iso_day(check_out)
    if not city or not check_in or not check_out:
        return []
    nights = _nights(check_in, check_out)
    arguments = {
        "location": f"{city}{near}" if near and not near.startswith(city) else (near or city),
        "check_in": check_in,
        "check_out": check_out,
        "currency": "CNY",
        "guest_nationality": "CN",
        "guests": max(1, int(guests or 2)),
        "rooms": max(1, int(rooms or 1)),
        "limit": 30,
    }
    if near:
        arguments["radius_meters"] = max(1000, int(radius or 3000))
    try:
        async with Session(_URL, _TOOLS) as client:
            data = await client.call("search_hotels", arguments)
    except Exception:
        return []
    hotels = []
    rows = data.get("hotels") if isinstance(data, dict) else None
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not row.get("name"):
            continue
        price = row.get("price")
        nightly = round(price / nights) if isinstance(price, (int, float)) and nights else None
        hotels.append({
            "name": str(row.get("name")),
            "address": str(row.get("address") or ""),
            "city": str(row.get("city") or city),
            "price": price,
            "nightly": nightly,
            "nights": nights,
            "currency": str(row.get("currency") or "CNY"),
            "star": row.get("starRating"),
            "rating": row.get("rating"),
            "reviews": row.get("reviewCount"),
            "url": str(row.get("url") or ""),
            "over_budget": bool(max_price > 0 and nightly and nightly > max_price),
        })
    hotels.sort(key=lambda item: item["nightly"] if item["nightly"] is not None else 10 ** 9)
    return hotels[:10]


async def search_rollinggo(
    city: str,
    check_in: str = "",
    check_out: str = "",
    near: str = "",
    max_price: int = 0,
    guests: int = 2,
    radius: int = 2000,
) -> list[dict]:
    """查 RollingGo 上这个地点附近的中文酒店，最多 10 家，按每晚展示价从低到高。

    没配 ROLLINGGO_API_KEY 时返回空列表，调用方再去用 Pilot。
    max_price 是每晚上限。超过的不删，标上 over_budget。
    """
    key = env("ROLLINGGO_API_KEY")
    city = (city or "").strip()
    near = (near or "").strip()
    check_in = iso_day(check_in)
    check_out = iso_day(check_out)
    if not key or not city or not check_in or not check_out:
        return []
    nights = _nights(check_in, check_out)
    place = f"{city}{near}" if near and not near.startswith(city) else (near or city)
    arguments = {
        "originQuery": f"{place}酒店",
        "place": place,
        "placeType": "景点" if near else "城市",
        "countryCode": "CN",
        "size": 15,
        "checkInParam": {
            "checkInDate": check_in,
            "stayNights": nights,
            "adultCount": max(1, int(guests or 2)),
        },
    }
    if near:
        arguments["filterOptions"] = {
            "distanceInMeter": max(500, min(int(radius or 2000), 5000)),
        }
    try:
        async with Session(
            _ROLLING_URL,
            _ROLLING_TOOLS,
            {"Authorization": f"Bearer {key}"},
        ) as client:
            data = await client.call("searchHotels", arguments)
    except Exception:
        return []
    hotels = []
    for row in rows_of(data):
        name = str(row.get("name") or row.get("hotelName") or "").strip()
        if not name:
            continue
        nightly = _nightly(row.get("price"))
        distance = row.get("distanceInMeters")
        hotels.append({
            "name": name,
            "address": str(row.get("address") or ""),
            "city": city,
            "nightly": nightly,
            "nights": nights,
            "price": round(nightly * nights) if nightly else None,
            "currency": "CNY",
            "star": row.get("starRating"),
            "distance": f"{int(distance)}米" if isinstance(distance, (int, float)) else "",
            "url": str(row.get("bookingUrl") or ""),
            "over_budget": bool(max_price > 0 and nightly and nightly > max_price),
        })
    hotels.sort(key=lambda item: item["nightly"] if item["nightly"] is not None else 10 ** 9)
    return hotels[:10]


def rows_of(data) -> list[dict]:
    """取出酒店列表。RollingGo 放在 hotelInformationList 里。"""
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    rows = data.get("hotelInformationList") or data.get("hotels") or []
    return [item for item in rows if isinstance(item, dict)] if isinstance(rows, list) else []


def _nightly(price) -> int | None:
    """每晚展示价。price 是对象时读 lowestPrice；没有价或标了没价就返回 None。"""
    if isinstance(price, (int, float)):
        return round(price)
    if not isinstance(price, dict) or price.get("hasPrice") is False:
        return None
    amount = price.get("lowestPrice")
    if isinstance(amount, (int, float)):
        return round(amount)
    return None


def _nights(check_in: str, check_out: str) -> int:
    """入住到离店隔了几晚。日期反了或相同就算 1 晚。"""
    try:
        days = (date.fromisoformat(check_out) - date.fromisoformat(check_in)).days
    except ValueError:
        return 1
    return days if days > 0 else 1
