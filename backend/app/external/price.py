import re

import httpx

from app.external.mcp_client import env
from app.external.search import search_web

"""景点。开放时间走高德，票价和要不要提前买从网页摘录里看。

这里只记下看到的事实，不提供预约或购票。摘录里没有的价格留空。
酒店房价在 hotel.py，餐馆在 food.py。两路都没有时返回空列表。
"""

_PLACE_URL = "https://restapi.amap.com/v3/place/text"


async def search_attractions(city: str, keyword: str = "", category: str = "") -> list[dict]:
    """按城市查景点。开放时间、票价、要不要提前买。最多 8 条。

    票价和提前买只从摘录里认，认不出就留空，不当成已经预约。
    """
    city = _place(city)
    keyword = (keyword or "").strip()
    category = (category or "").strip()
    words = " ".join(part for part in (keyword, category) if part) or "景点"
    places = await _amap(words, city, "" if keyword else "110000")
    notes = await search_web(" ".join(part for part in (city, words, "门票", "价格", "开放时间", "预约") if part))
    return _merge(places, notes, "景点")


async def _amap(keywords: str, city: str, types: str) -> list[dict]:
    key = env("AMAP_KEY")
    if not key or not keywords:
        return []
    params = {
        "key": key,
        "keywords": keywords,
        "extensions": "all",
        "offset": 8,
        "page": 1,
    }
    if city:
        params["city"] = city
    if types:
        params["types"] = types
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
            "open_time": _text(business.get("opentime2") or business.get("open_time")),
            "rating": _text(business.get("rating")),
            "price": _yuan(_text(business.get("cost") or business.get("lowest_price"))),
            "notice": "",
            "url": "",
            "excerpt": "",
        })
        if len(places) >= 8:
            break
    return places


def _merge(places: list[dict], notes: list[dict], kind: str) -> list[dict]:
    notes = [note for note in notes if isinstance(note, dict)]
    if not places:
        rows = []
        for note in notes[:5]:
            title = _text(note.get("title"))
            if not title:
                continue
            rows.append({
                "name": title[:40],
                "address": "",
                "open_time": "",
                "rating": "",
                "price": "",
                "notice": "",
                "url": _text(note.get("url")),
                "excerpt": _text(note.get("snippet"))[:300],
            })
        return [_read_ticket(row) for row in rows]
    for place in places:
        note = _match(place["name"], notes)
        if not note:
            continue
        place["excerpt"] = _text(note.get("snippet"))[:300]
        if not place["url"]:
            place["url"] = _text(note.get("url"))
    if notes and not any(place["excerpt"] for place in places):
        places[0]["excerpt"] = _text(notes[0].get("snippet"))[:300]
        if not places[0]["url"]:
            places[0]["url"] = _text(notes[0].get("url"))
    for place in places:
        place["kind"] = kind
        _read_ticket(place)
    return places


def _read_ticket(place: dict) -> dict:
    """从摘录里补票价，以及要不要提前买。没有就保持空白，不去预约。"""
    text = _text(place.get("excerpt"))
    if not place.get("price"):
        place["price"] = _price(text)
    if not place.get("notice"):
        place["notice"] = _notice(text)
    return place


def _price(text: str) -> str:
    """认「门票60元」「门市价¥70起」。条目序号和电话不算票价。"""
    if not text:
        return ""
    named = re.search(
        r"(?:门票|票价|门市价|成人票)[^。\n\d]{0,8}[¥￥]?\s*(\d+(?:\.\d+)?)(?:\s*元|\s*起)",
        text,
    )
    if named:
        return _yuan(named.group(1))
    marked = re.search(r"[¥￥]\s*(\d+(?:\.\d+)?)", text)
    if marked:
        return _yuan(marked.group(1))
    return ""


def _notice(text: str) -> str:
    """只记录摘录里已经写明的购买提示。不发起预约。"""
    if not text:
        return ""
    if re.search(r"无需预约|不用预约|免预约", text):
        return "不用预约"
    early = re.search(r"需提前\s*(\d+)\s*天", text)
    if early:
        return f"需提前{early.group(1)}天购票"
    if re.search(r"需预约|须预约|需要预约|提前预约|提前购票", text):
        return "需要预约"
    return ""


def _yuan(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if text.endswith("元"):
        return text
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return f"{text}元"
    return ""


def _match(name: str, notes: list[dict]) -> dict | None:
    short = name.split("(")[0].split("（")[0]
    for note in notes:
        blob = _text(note.get("title")) + _text(note.get("snippet"))
        if name in blob or (len(short) >= 2 and short in blob):
            return note
    return None


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
