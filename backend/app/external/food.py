import httpx

from app.external.mcp_client import env

"""餐馆。用已经配好的高德餐饮数据，也就是高德美食查询那一路的来源。

返回店名、评分、人均、营业时间。人均没有时留空。没配高德密钥或请求失败时返回空列表。
"""

_PLACE_URL = "https://restapi.amap.com/v3/place/text"


async def search_food(city: str, keyword: str = "") -> list[dict]:
    """按城市和菜系查餐馆。keyword 可以是火锅、日料，不填就搜餐厅。最多 8 家。"""
    city = _place(city)
    keyword = (keyword or "").strip() or "餐厅"
    key = env("AMAP_KEY")
    if not city or not key:
        return []
    params = {
        "key": key,
        "keywords": keyword,
        "city": city,
        "types": "050000",
        "extensions": "all",
        "offset": 8,
        "page": 1,
    }
    try:
        async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
            response = await client.get(_PLACE_URL, params=params)
            response.raise_for_status()
            data = response.json()
    except Exception:
        return []
    places = []
    for row in data.get("pois") or []:
        if not isinstance(row, dict):
            continue
        business = row.get("biz_ext") if isinstance(row.get("biz_ext"), dict) else {}
        name = _text(row.get("name"))
        if not name:
            continue
        places.append({
            "name": name,
            "address": _text(row.get("address")),
            "rating": _text(business.get("rating")),
            "cost": _text(business.get("cost")),
            "open_time": _text(business.get("opentime2") or business.get("open_time")),
            "tel": _text(row.get("tel")),
        })
        if len(places) >= 8:
            break
    return places


def _place(name: str) -> str:
    text = (name or "").strip()
    if text.endswith("市") and len(text) > 1:
        return text[:-1]
    return text


def _text(value) -> str:
    if value is None or value == [] or value == {}:
        return ""
    text = str(value).strip()
    if text in ("[]", "{}"):
        return ""
    return text
