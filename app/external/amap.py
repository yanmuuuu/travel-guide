from urllib.parse import quote

import httpx

from app.external.mcp_client import Session, env, rows

"""高德地图。把行程画成专属地图，并查地点的开放时间、评分和人均。

密钥用已经配好的 AMAP_KEY。
门票怎么买、要不要预约不在这路数据里。没配密钥或请求失败时返回空。
"""

_TOOLS = (
    "maps_geo",
    "maps_text_search",
    "maps_around_search",
    "maps_search_detail",
    "maps_schema_personal_map",
    "maps_direction_walking",
    "maps_direction_driving",
    "maps_direction_transit_integrated",
)
_REST_TEXT = "https://restapi.amap.com/v3/place/text"
# 附近搜索用 v5：show_fields=business 能多拿到类型标签（快捷、商务、民宿）和电话，v3 没有。
_REST_AROUND = "https://restapi.amap.com/v5/place/around"
_ROUTE_TOOLS = {
    "walking": "maps_direction_walking",
    "driving": "maps_direction_driving",
    "transit": "maps_direction_transit_integrated",
}


async def draw_route(title: str, days: list[dict], city: str = "") -> dict:
    """按每天的停留顺序生成高德专属地图链接。

    days 里每一天是 {"title": "第1天", "stops": ["故宫", "景山"]}。
    停留点也可以直接给坐标：{"name": "故宫", "lon": 116.39, "lat": 39.91, "poi_id": ""}。
    一天最多 16 个点。返回 {"title", "url"}，生成不了就是空字典。
    """
    title = (title or "").strip() or "行程"
    city = (city or "").strip()
    if not env("AMAP_KEY"):
        return {}
    line_list = []
    try:
        async with Session(_url(), _TOOLS) as client:
            for day in days or []:
                if not isinstance(day, dict):
                    continue
                points = []
                for stop in day.get("stops") or []:
                    try:
                        point = await _point(client, stop, city)
                    except Exception:
                        point = None
                    if point:
                        points.append(point)
                points = _cap(points)
                if points:
                    line_list.append({
                        "title": (day.get("title") or f"第{len(line_list) + 1}天").strip(),
                        "pointInfoList": points,
                    })
            if not line_list:
                return {}
            data = await client.call("maps_schema_personal_map", {
                "orgName": title,
                "lineList": line_list,
                "sceneType": 1,
            })
    except Exception:
        return {}
    url = _first_url(data)
    if not url:
        return {}
    return {"title": title, "url": url}


async def route(origin: str, destination: str, mode: str = "walking", city: str = "", city_to: str = "") -> dict:
    """两点之间的步行、驾车或公交方案。坐标写成「经度,纬度」。"""
    origin = (origin or "").strip()
    destination = (destination or "").strip()
    tool = _ROUTE_TOOLS.get(mode)
    if not origin or not destination or not tool or not env("AMAP_KEY"):
        return {}
    arguments = {"origin": origin, "destination": destination}
    if tool == "maps_direction_transit_integrated":
        arguments["city"] = (city or "").strip() or "北京"
        arguments["cityd"] = (city_to or city or "").strip() or arguments["city"]
    try:
        async with Session(_url(), _TOOLS) as client:
            data = await client.call(tool, arguments)
    except Exception:
        return {}
    return _route_summary(data)


async def search_places(keywords: str, city: str = "") -> list[dict]:
    """按关键词搜地点。返回名称、编号、地址和坐标，不含评分。最多 10 条。

    走高德 REST。MCP 的 maps_text_search 不带坐标，画地图和查路线都要坐标。
    """
    keywords = (keywords or "").strip()
    key = env("AMAP_KEY")
    if not keywords or not key:
        return []
    params = {"key": key, "keywords": keywords, "offset": 10, "page": 1, "extensions": "base"}
    city = (city or "").strip()
    if city:
        params["city"] = city
        params["citylimit"] = "true"
    data = await _rest(_REST_TEXT, params)
    found = [_place_row(row) for row in data.get("pois") or [] if isinstance(row, dict)]
    return [place for place in found if place["name"]][:10]


async def locate(name: str, city: str = "") -> dict | None:
    """地名换成一个带坐标的地点。没搜到就是 None。"""
    for place in await search_places(name, city):
        if place["lon"] and place["lat"]:
            return place
    return None


async def nearby(center: str, city: str, kind: str, keyword: str = "", radius: int = 1500) -> tuple[dict | None, list[dict]]:
    """查某个地点周围的酒店或餐馆，按距离从近到远。最多 10 条。

    kind 用 hotel 或 food。返回 (中心点, 列表)。中心点找不到时列表为空。
    每条有：名称、距离、类型、评分、人均、当天营业时间、电话、地址。

    价格：文档里酒店也有 cost，但实测各城市酒店几乎都是空的，房价要看 hotel.py。
    餐馆的 cost 是人均，基本都有。
    """
    point = await locate(center, city)
    key = env("AMAP_KEY")
    types = {"hotel": "100000", "food": "050000"}.get(kind, "")
    if not point or not key or not types:
        return point, []
    params = {
        "key": key,
        "location": point["location"],
        "types": types,
        "radius": max(300, min(int(radius or 1500), 5000)),
        "sortrule": "distance",
        "page_size": 10,
        "page_num": 1,
        "show_fields": "business",
    }
    keyword = (keyword or "").strip()
    if keyword:
        params["keywords"] = keyword
    data = await _rest(_REST_AROUND, params)
    places = []
    for row in data.get("pois") or []:
        # 高德会把挂在酒店、餐馆名下的小卖部、特产店也带出来，按分类编码前两位再筛一遍
        if not isinstance(row, dict) or not _text(row.get("typecode")).startswith(types[:2]):
            continue
        business = row.get("business") if isinstance(row.get("business"), dict) else {}
        places.append({
            "name": _text(row.get("name")),
            "distance": _meters(row.get("distance")),
            # keytag 是「快捷酒店」「民宿」「火锅」这种短标签，比 type 末段好懂
            "type": _text(business.get("keytag")) or _text(row.get("type")).split(";")[-1],
            "rating": _empty(business.get("rating")),
            "cost": _empty(business.get("cost")),
            "open_time": _empty(business.get("opentime_today")),
            "tel": _empty(str(business.get("tel") or "").split(";")[0]),
            "address": _empty(row.get("address")),
        })
    return point, [place for place in places if place["name"]]


async def _rest(url: str, params: dict) -> dict:
    """调一次高德 REST。失败或状态不是 1 时返回空字典。"""
    try:
        async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
    except Exception:
        return {}
    if not isinstance(data, dict) or str(data.get("status")) != "1":
        return {}
    return data


async def place_detail(poi_id: str) -> dict:
    """查一个地点的地址、开放时间、评分和人均。没有的字段留空。"""
    poi_id = (poi_id or "").strip()
    if not poi_id or not env("AMAP_KEY"):
        return {}
    try:
        async with Session(_url(), _TOOLS) as client:
            data = await client.call("maps_search_detail", {"id": poi_id})
    except Exception:
        return {}
    found = rows(data, "pois")
    if found and (found[0].get("name") or found[0].get("id")):
        row = found[0]
    elif isinstance(data, dict) and (data.get("name") or data.get("id")):
        row = data
    else:
        row = found[0] if found else {}
    if not isinstance(row, dict):
        return {}
    detail = _place_row(row)
    business = _business(row)
    detail.update({
        "open_time": _text(business.get("opentime_week") or business.get("opentime_today") or business.get("opentime")),
        "rating": _text(business.get("rating")),
        "cost": _text(business.get("cost")),
        "tel": _text(row.get("tel") or business.get("tel")),
    })
    return detail


async def search_restaurants(city: str, keyword: str = "") -> list[dict]:
    """搜餐馆，并补上评分、人均和营业时间。最多 5 家。"""
    words = (keyword or "").strip() or "餐厅"
    return await _with_detail(words, city)


async def search_lodging(city: str, keyword: str = "") -> list[dict]:
    """搜酒店的评分和营业时间。可订房价在 hotel.py。最多 5 家。"""
    words = (keyword or "").strip()
    words = f"{words} 酒店" if words else "酒店"
    return await _with_detail(words, city)


async def _with_detail(keywords: str, city: str) -> list[dict]:
    hits = await search_places(keywords, city)
    detailed = []
    for hit in hits[:5]:
        extra = await place_detail(hit.get("id") or "")
        if extra:
            hit.update({key: value for key, value in extra.items() if value})
        detailed.append(hit)
    return detailed


async def _point(client: Session, stop, city: str) -> dict | None:
    if isinstance(stop, str):
        stop = {"name": stop}
    if not isinstance(stop, dict):
        return None
    name = _text(stop.get("name"))
    lon = stop.get("lon")
    lat = stop.get("lat")
    poi_id = _text(stop.get("poi_id") or stop.get("poiId"))
    if lon in (None, "") or lat in (None, ""):
        found = await _find_place(client, name, city)
        if not found:
            return None
        name = name or found["name"]
        lon, lat = found["lon"], found["lat"]
        poi_id = poi_id or found["id"]
    if not name or lon in (None, "") or lat in (None, ""):
        return None
    point = {"name": name, "lon": float(lon), "lat": float(lat)}
    if poi_id:
        point["poiId"] = poi_id
    return point


async def _find_place(client: Session, name: str, city: str) -> dict | None:
    """画地图时把地名换坐标。client 留着是为了和调用方的写法一致，坐标走 REST。"""
    if not name:
        return None
    return await locate(name, city)


def _place_row(row: dict) -> dict:
    lon, lat = _split_location(row.get("location"))
    return {
        "name": _text(row.get("name")),
        "id": _text(row.get("id") or row.get("poiid")),
        "address": _text(row.get("address")),
        "location": _text(row.get("location")),
        "lon": lon,
        "lat": lat,
        "type": _text(row.get("type")),
    }


def _split_location(value) -> tuple[str, str]:
    text = _text(value)
    pieces = text.split(",")
    if len(pieces) != 2:
        return "", ""
    try:
        return str(float(pieces[0])), str(float(pieces[1]))
    except ValueError:
        return "", ""


def _business(row: dict) -> dict:
    for key in ("business", "biz_ext"):
        value = row.get(key)
        if isinstance(value, dict):
            return value
    return row


def _route_summary(data) -> dict:
    path = _path(data)
    if not path:
        return {}
    steps = _transit_steps(path) if path.get("segments") else []
    for step in path.get("steps") or []:
        if not isinstance(step, dict):
            continue
        instruction = _text(step.get("instruction") or step.get("road"))
        if instruction:
            steps.append(instruction)
        if len(steps) >= 8:
            break
    # 公交方案自己不带总距离，总距离在外层。
    outer = data.get("route") if isinstance(data.get("route"), dict) else data
    distance = path.get("distance") or outer.get("distance")
    summary = {
        "distance": _meters(distance),
        "duration": _seconds(path.get("duration")),
        "steps": steps,
    }
    if not summary["distance"] and not summary["duration"] and not steps:
        return {}
    return summary


def _path(data) -> dict:
    if not isinstance(data, dict):
        return {}
    route = data.get("route") if isinstance(data.get("route"), dict) else data
    paths = route.get("paths") or route.get("transits")
    if isinstance(paths, list) and paths and isinstance(paths[0], dict):
        # 公交会给好几套方案，取用时最短的那套。
        return min(
            (item for item in paths if isinstance(item, dict)),
            key=lambda item: _number(item.get("duration")),
        )
    if isinstance(route, dict) and ("distance" in route or "duration" in route):
        return route
    return {}


def _transit_steps(path: dict) -> list[str]:
    """公交方案收成「步行 1.0公里 → 7路 灵隐公交总站到三元坊1」这样几段。"""
    steps = []
    for segment in path.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        walking = segment.get("walking") if isinstance(segment.get("walking"), dict) else {}
        meters = _number(walking.get("distance"))
        if 100 <= meters < float("inf"):
            steps.append(f"步行{_meters(meters)}")
        bus = segment.get("bus") if isinstance(segment.get("bus"), dict) else {}
        for line in bus.get("buslines") or []:
            if not isinstance(line, dict):
                continue
            name = _text(line.get("name")).split("(")[0]
            start = _text((line.get("departure_stop") or {}).get("name"))
            end = _text((line.get("arrival_stop") or {}).get("name"))
            if name:
                steps.append(f"{name} {start}到{end}".strip())
            break
        railway = segment.get("railway") if isinstance(segment.get("railway"), dict) else {}
        if _text(railway.get("name")):
            steps.append(_text(railway.get("name")))
    return steps


def _number(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("inf")


def _cap(points: list[dict]) -> list[dict]:
    if len(points) <= 16:
        return points
    return points[:15] + points[-1:]


def _meters(value) -> str:
    try:
        meters = int(float(value))
    except (TypeError, ValueError):
        return ""
    if meters >= 1000:
        return f"{meters / 1000:.1f}公里"
    return f"{meters}米"


def _seconds(value) -> str:
    try:
        seconds = int(float(value))
    except (TypeError, ValueError):
        return ""
    minutes = max(1, round(seconds / 60)) if seconds else 0
    if minutes >= 60:
        spare = minutes % 60
        return f"{minutes // 60}小时{spare}分钟" if spare else f"{minutes // 60}小时"
    return f"{minutes}分钟"


def _first_url(data) -> str:
    # 专属地图回的是 amapuri:// 开头的链接，要在手机高德里打开，不是网页。
    if isinstance(data, str) and data.strip().startswith(("http", "amapuri://")):
        return data.strip()
    if isinstance(data, dict):
        for key in ("uri", "url", "qr_code_url", "link"):
            found = _first_url(data.get(key))
            if found:
                return found
        for value in data.values():
            found = _first_url(value)
            if found:
                return found
    if isinstance(data, list):
        for item in data:
            found = _first_url(item)
            if found:
                return found
    return ""


def _url() -> str:
    return "https://mcp.amap.com/mcp?key=" + quote(env("AMAP_KEY"), safe="")


def _text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _empty(value) -> str:
    """高德空字段会给 []，收成空字符串。"""
    if value in (None, [], {}):
        return ""
    return _text(value)
