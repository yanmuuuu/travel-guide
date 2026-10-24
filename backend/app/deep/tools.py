import asyncio
import threading

from langchain_core.tools import tool
from langgraph.types import interrupt

from app.external.amap import draw_route, locate, nearby, place_detail, route
from app.external.flight import search_flights
from app.external.food import search_food
from app.external.hotel import search_hotels, search_rollinggo
from app.external.mcp_client import env
from app.external.price import search_attractions
from app.external.rail import query_trains, query_transfer
from app.external.search import search_web
from app.external.weather import forecast
from app.external.xhs import search_notes

"""给子代理调用的查询。按子代理分成四组，主代理只有 ask。

- 路线：weather、sight、place、road、route_map、notes、web
- 交通：train、transfer、flight
- 住宿：hotel、place
- 吃饭：food、place、notes

查不到就返回一句「没查到」，不往外抛，让代理把这一项写成待核实。
"""


# ---------- 公共小工具 ----------

def _run(coro):
    """同步工具里跑异步查询。外面已经有事件循环时，改到新线程里跑。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    box = {}

    def runner():
        try:
            box["value"] = asyncio.run(coro)
        except Exception as exc:
            box["error"] = exc

    thread = threading.Thread(target=runner)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _safe(coro, empty):
    """查询抛错时交回空结果，不让整张图停在这一条工具上。"""
    try:
        value = _run(coro)
    except Exception:
        return empty
    return empty if value is None else value


def _price_text(prices) -> str:
    """席别或舱位的价格收成一句。没有就写待核实。"""
    if not isinstance(prices, dict) or not prices:
        return "票价待核实"
    parts = []
    for name, value in list(prices.items())[:4]:
        if value in (None, ""):
            continue
        parts.append(f"{name}{value}")
    return " ".join(parts) if parts else "票价待核实"


def _fare_text(fares: list[dict]) -> str:
    """一趟车的全部席别：余票、原价、折后价。卖完的也列出来，写「无票」。"""
    if not fares:
        return "票价待核实"
    parts = []
    for row in fares:
        left = row.get("left") or "余票待核实"
        if left == "无":
            left = "无票"
        elif left == "有":
            left = "有票"
        elif left.isdigit():
            left = f"剩{left}张"
        price = row.get("price")
        if price is None:
            money = "票价待核实"
        elif row.get("discount"):
            money = f"原价{price:g}元 {row['discount'] / 10:g}折 约{row['final']}元"
        else:
            money = f"{price:g}元（折扣待核实）"
        parts.append(f"{row['seat']} {left} {money}")
    return "；".join(parts)


def _cheapest_fare(fares: list[dict]) -> float:
    """有票的席别里，折后最低的那个价。全卖完了就当很贵，不会被标成最便宜。"""
    prices = [
        row["final"] for row in fares or []
        if row.get("final") is not None and row.get("left") not in ("无", "")
    ]
    return min(prices) if prices else 10 ** 9


def _yuan(value) -> float:
    """票价字符串换成数字，比较便宜贵用。读不出就当很贵。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 10 ** 9


# ---------- 路线：天气、景点、市内交通、地图 ----------

@tool
def weather(city: str, day: str, days: int = 1) -> str:
    """查这座城市从 day 开始连续 days 天的天气。day 写 YYYY-MM-DD。只能查今天起 40 天内。"""
    text = _safe(forecast(city, day, days), "")
    if not str(text).strip():
        return "没有查到天气预报。出发日可能超过 40 天，写天气待核实，不要编。"
    return str(text).strip()


@tool
def sight(city: str, keyword: str = "") -> str:
    """查景点的开放时间、票价、要不要提前买。keyword 不填就是城市热门景点。最多 8 个。不预约。"""
    rows = _safe(search_attractions(city, keyword), [])
    if not rows:
        return "没有查到景点。不要编开放时间和票价。"
    lines = []
    for row in rows[:8]:
        lines.append(
            f"{row.get('name')} | 开放：{row.get('open_time') or '待核实'} "
            f"| 票价：{row.get('price') or '待核实'} | 提前买：{row.get('notice') or '摘录没写'} "
            f"| 评分{row.get('rating') or '未知'}"
        )
    return "\n".join(lines)


@tool
def place(name: str, city: str) -> str:
    """查一个具体地点的详情：地址、每周营业或开放时间、评分、人均、电话。用高德。

    sight 列出来的开放时间不全、或想确认某家店和酒店时用。name 写中文全名更准。
    """
    found = _safe(locate(name, city), None)
    if not found or not found.get("id"):
        return f"高德没找到「{name}」，换一个更准的名字再查。"
    detail = _safe(place_detail(found["id"]), {})
    if not detail:
        return f"{found['name']} 地址：{found.get('address') or '待核实'}。详情没查到。"
    return (
        f"{detail.get('name') or found['name']} | 地址：{detail.get('address') or found.get('address') or '待核实'} "
        f"| 开放：{detail.get('open_time') or '待核实'} | 评分{detail.get('rating') or '未知'} "
        f"| 人均{detail.get('cost') or '待核实'} | 电话{detail.get('tel') or '待核实'}"
    )


@tool
def notes(keyword: str) -> str:
    """搜小红书笔记，看真实玩法、排队情况、避坑和招牌菜。keyword 写「城市 景点或菜」，不要整句。最多 2 篇。

    需要本机先开着小红书服务。笔记是个人经验，价格和开放时间仍以 sight、place 为准。
    """
    rows = _safe(search_notes(keyword), [])
    if not rows:
        return "小红书没有返回笔记。服务没开、没登录或没搜到都会这样。"
    return "\n\n".join(
        f"{row.get('title')}\n{str(row.get('snippet') or '')[:400]}\n{row.get('url')}"
        for row in rows
    )


@tool
def web(query: str) -> str:
    """搜网页（Tavily）。用来补政策、闭馆通知、节假日限流、演出时间这些高德和小红书没有的。最多 5 条。

    query 写短词，例如「杭州 灵隐寺 预约 2026」。
    """
    rows = _safe(search_web(query), [])
    if not rows:
        return "网页没有搜到结果。不要编。"
    return "\n\n".join(
        f"{row.get('title')}\n{str(row.get('snippet') or '')[:300]}\n{row.get('url')}"
        for row in rows[:5]
    )


@tool
def road(start: str, end: str, city: str, mode: str = "transit") -> str:
    """查市内两点怎么走。mode 用 transit（公交地铁）、walking 或 driving。地点写中文名。

    两地 1.5 公里内先用 walking。返回距离、用时和换乘。
    """
    start_point = _safe(locate(start, city), None)
    end_point = _safe(locate(end, city), None)
    if not start_point or not end_point:
        return f"高德没找到「{start}」或「{end}」的坐标，换一个更准的名字再查。"
    mode = mode if mode in ("walking", "driving", "transit") else "transit"
    found = _safe(route(start_point["location"], end_point["location"], mode, city, city), {})
    if not found:
        return "没有查到市内路线。不要编距离和时间。"
    steps = " → ".join(found.get("steps") or [])
    return (
        f"{start_point['name']} 到 {end_point['name']}（{mode}）："
        f"{found.get('distance') or '距离未知'}，{found.get('duration') or '用时未知'}。{steps}"
    )


@tool
def route_map(title: str, city: str, days: list[list[str]]) -> str:
    """把每天的停留顺序画成高德专属地图。days 是每天的地点列表，例如 [["灵隐寺","西湖"],["西溪湿地"]]。

    返回 amapuri:// 链接，用手机高德打开。一天最多 16 个点。
    """
    plan = [
        {"title": f"第{index}天", "stops": [stop for stop in stops if str(stop).strip()]}
        for index, stops in enumerate(days or [], 1)
    ]
    found = _safe(draw_route(title, plan, city), {})
    if not found.get("url"):
        return "专属地图没生成，可能是地名对不上。终稿里不放地图链接。"
    return f"专属地图：{found['url']}（用手机高德打开）"


# ---------- 交通：火车、中转、飞机 ----------

@tool
def train(
    origin: str,
    destination: str,
    day: str,
    depart_after: str = "",
    arrive_before: str = "",
    kind: str = "",
) -> str:
    """查一天里的全部直达火车，再按条件筛。

    origin、destination 写城市名，会包含同城所有车站（如汉口、武汉、武昌）。
    depart_after、arrive_before 写 HH:MM，例如去程要中午前到就写 arrive_before="12:00"。
    kind 写车型首字母，例如 "G"、"GD"。不填就不限。
    最多列 10 趟，并标出最快和最便宜的。一趟都没有时，自动查中转。
    每趟列出全部席别（商务座、一等座、二等座、无座等）的余票、原价、折扣和折后价。
    「最便宜」按还有票的席别里折后最低的算。
    """
    rows = _safe(query_trains(origin, destination, day), [])
    if len(rows) == 1 and rows[0].get("note") and not rows[0].get("train_code"):
        return f"{rows[0]['note']}。照这句原话交回，写待核实，不要编班次，也不要自己猜别的原因。"
    if not rows:
        return transfer.invoke({"origin": origin, "destination": destination, "day": day})
    kinds = (kind or "").upper().strip()
    picked = []
    for row in rows:
        code = str(row.get("train_code") or "")
        start = str(row.get("start_time") or "")
        arrive = str(row.get("arrive_time") or "")
        if kinds and code[:1] not in kinds:
            continue
        if depart_after and start and start < depart_after:
            continue
        # 到达时刻比出发还早，说明是第二天到，arrive_before 按当天算就不合格。
        if arrive_before and arrive and (arrive > arrive_before or arrive < start):
            continue
        picked.append(row)
    head = f"{day} {origin}到{destination}：直达共{len(rows)}趟，符合条件{len(picked)}趟。"
    if not picked:
        return head + "条件太紧，放宽时间或车型再查一次。"
    picked.sort(key=lambda row: str(row.get("start_time") or ""))
    fastest = min(picked, key=lambda row: str(row.get("duration") or "99:99"))
    cheapest = min(picked, key=lambda row: _cheapest_fare(row.get("fares")))
    lines = [head]
    for row in picked[:10]:
        marks = []
        if row is fastest:
            marks.append("最快")
        if row is cheapest:
            marks.append("最便宜")
        mark = f"【{'、'.join(marks)}】" if marks else ""
        lines.append(
            f"{row.get('train_code') or '车次待核实'}{mark} "
            f"{row.get('from_station') or origin} {row.get('start_time') or '?'} → "
            f"{row.get('to_station') or destination} {row.get('arrive_time') or '?'} "
            f"历时{row.get('duration') or '?'}\n    {_fare_text(row.get('fares'))}"
        )
    if len(picked) > 10:
        lines.append(f"还有{len(picked) - 10}趟没列出，需要可以加时间条件再查。")
    return "\n".join(lines)


@tool
def transfer(origin: str, destination: str, day: str, middle: str = "") -> str:
    """查火车中转。没有直达，或直达都不合适时用。middle 可指定中转站。最多 10 套，按总时长排。"""
    plans = _safe(query_transfer(origin, destination, day, middle), [])
    if len(plans) == 1 and plans[0].get("note"):
        return f"{plans[0]['note']}。照这句原话交回，写待核实，不要编班次，也不要自己猜别的原因。"
    if not plans:
        return "没有直达，也没有查到中转。考虑飞机或写待核实。"
    lines = [f"{day} {origin}到{destination} 中转方案："]
    for plan in plans[:10]:
        legs = "；".join(
            f"{leg['train_code']} {leg['from_station']}{leg['start_time']}→{leg['to_station']}{leg['arrive_time']}（{leg['seats'] or '余票待核实'}）"
            for leg in plan["segments"]
        )
        lines.append(f"经{plan['middle']} 共{plan['total']} 换乘等{plan['wait']}：{legs}")
    return "\n".join(lines)


@tool
def flight(origin: str, destination: str, day: str) -> str:
    """查机票。城市用中文名，day 是出发日期。最多 10 班，按价格从低到高。"""
    rows = _safe(search_flights(origin, destination, day), [])
    if not rows:
        return "没有查到机票。城市没有机场、日期不对或没配密钥都会这样。不要编航班。"
    rows.sort(key=lambda row: min((_yuan(v) for v in (row.get("prices") or {}).values()), default=10 ** 9))
    lines = []
    for row in rows[:10]:
        lines.append(
            f"{row.get('flight_no') or '航班待核实'} {row.get('airline') or ''} "
            f"{row.get('depart') or '时刻待核实'} → {row.get('arrive') or '?'} | {_price_text(row.get('prices'))}"
        )
    return "\n".join(lines)


# ---------- 住宿：沿路线找酒店 ----------

@tool
def hotel(
    city: str,
    near: str,
    check_in: str,
    check_out: str,
    max_per_night: int = 0,
    guests: int = 2,
    rooms: int = 1,
) -> str:
    """在某个地点附近找酒店。near 写当晚最后一站或第二天第一站，例如「河坊街」。

    日期写 YYYY-MM-DD。max_per_night 是每晚上限，超出的会标出来。
    先查 RollingGo（中文店名、每晚展示价）。结果少于 3 家时再补 Pilot（英文店名）。
    高德只补充步行范围内的店名、类型和电话，房价基本是待核实。
    """
    lines = []
    if not env("ROLLINGGO_API_KEY"):
        lines.append("没配 ROLLINGGO_API_KEY，中文酒店房价查不到。")
        rolling = []
    else:
        rolling = _safe(search_rollinggo(city, check_in, check_out, near, max_per_night, guests), [])
    if rolling:
        lines.append(f"{near}附近可订（RollingGo，每晚展示价，按价格从低到高，下单前再核对）：")
        lines.extend(_hotel_line(row) for row in rolling[:10])
    if len([row for row in rolling if row.get("nightly")]) < 3:
        priced = _safe(search_hotels(city, check_in, check_out, near, max_per_night, guests, rooms), [])
        if priced:
            lines.append(f"{near}附近可订（Pilot，英文店名，每晚价）：")
            lines.extend(_hotel_line(row, with_rating=True) for row in priced[:10])
        elif not rolling:
            lines.append(f"{near}附近没有查到能报价的酒店。")
    _, around = _safe(nearby(near, city, "hotel", radius=1500), (None, []))
    if around:
        lines.append(f"{near}步行范围内的酒店（高德，房价待核实，可打电话问）：")
        for row in around[:4]:
            price = f"约{row['cost']}元" if row["cost"] else "房价待核实"
            lines.append(
                f"- {row['name']} 距离{row['distance'] or '?'} {row['type']} 评分{row['rating'] or '未知'} "
                f"{price} 电话{row['tel'] or '待核实'}"
            )
    return "\n".join(lines)


def _hotel_line(row: dict, with_rating: bool = False) -> str:
    """一行酒店：店名、每晚价、总价、星级、距离。Pilot 多带评分。"""
    nightly = f"每晚约{row['nightly']}元" if row.get("nightly") else "房价待核实"
    total = f"（{row.get('nights')}晚共{round(float(row.get('price') or 0))}元）" if row.get("price") else ""
    over = " 超出每晚上限" if row.get("over_budget") else ""
    distance = f" 距离{row['distance']}" if row.get("distance") else ""
    rating = f" 评分{row.get('rating') or '未知'}/10" if with_rating else ""
    return (
        f"- {row['name']} {nightly}{total} {row.get('star') or '?'}星{distance}{rating}{over}"
    )


# ---------- 吃饭：沿路线找餐馆 ----------

@tool
def food(city: str, near: str = "", keyword: str = "") -> str:
    """在某个景点附近找餐馆。near 写这一顿前后的那一站，例如「灵隐寺」。keyword 可写本帮菜、面馆。

    返回店名、距离、人均、评分。不填 near 就按整座城市搜。
    """
    if near:
        _, rows = _safe(nearby(near, city, "food", keyword=keyword, radius=1000), (None, []))
        if not rows:
            return f"{near}附近没有查到餐馆。不要编店名和人均。"
        lines = [f"{near}附近的餐馆："]
        for row in rows[:10]:
            lines.append(
                f"- {row['name']} 距离{row['distance'] or '?'} 人均{row['cost'] or '待核实'} "
                f"评分{row['rating'] or '未知'} {row['type']} 今天营业{row['open_time'] or '待核实'}"
            )
        return "\n".join(lines)
    rows = _safe(search_food(city, keyword), [])
    if not rows:
        return "没有查到餐馆。不要编店名和人均。"
    return "\n".join(
        f"- {row.get('name')} 人均{row.get('cost') or '待核实'} 评分{row.get('rating') or '未知'}"
        for row in rows[:10]
    )


# ---------- 主代理：停下来问人 ----------

@tool
def ask(question: str, options: list[str]) -> str:
    """需要用户拍板时调用。question 是一句问话。options 是选项列表，最多 10 个，没有就空列表。

    用在这些情况：多趟车或航班、多家酒店、雨雪要不要改期、总价超出预算。
    调用后图会停住，等用户回答再继续。
    """
    # interrupt 把问题交出去。恢复时 resume 里的内容会成为这个返回值。
    picked = interrupt({"question": question, "options": options})
    if isinstance(picked, dict):
        picked = picked.get("answer") or picked
    return str(picked)
